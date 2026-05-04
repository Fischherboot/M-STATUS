"""Active probing for devices and services.

One unified loop runs every CHECK_INTERVAL_SECONDS:

    Devices  — ICMP ping the IP we got from the overseer. Single ping per run
               (anything more is just noise). Counts consecutive failures.

    Services — Probe the configured endpoints:
                 * external service (is_local=False) → HTTP GET the URL
                 * local service    (is_local=True)  → TCP connect to
                   internal_host:internal_port
                   (the public URL is intentionally NOT probed for local
                   services — most are behind a Cloudflare Tunnel that
                   stays "up" even when the origin is broken, which would
                   give us a false green.)
               Several pings per run (smooths out flaky networks).

A run is "good" if all probes succeeded, "bad" otherwise. Fail-counters
escalate via the thresholds in config.py:

    fails == 0                 → green (today: green)
    1 ≤ fails < DEGRADED       → orange (today: orange)
    DEGRADED ≤ fails < OFFLINE → red, label "Fehlerhaft" (today: red)
    fails ≥ OFFLINE            → red, label "Offline"   (today: red)
"""
from __future__ import annotations

import asyncio
import logging
import platform
import shutil
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from . import config, db, status

log = logging.getLogger("m_status.monitor")


# ─── url helpers ──────────────────────────────────────────────────────────

def _normalise_url(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return raw
    if raw.startswith(("http://", "https://")):
        return raw
    return "https://" + raw


# ─── HTTP probe (services, external) ─────────────────────────────────────

async def _probe_http(client: httpx.AsyncClient, url: str) -> bool:
    """One HTTP probe. 2xx/3xx/4xx counts as 'reachable' (server alive); 5xx
    and connection errors count as down (the service is broken, not just
    rejecting our request)."""
    try:
        r = await client.get(url, follow_redirects=True,
                             timeout=config.PROBE_TIMEOUT_SECONDS)
        return 200 <= r.status_code < 500
    except (httpx.HTTPError, OSError):
        return False


# ─── TCP probe (services, local internal) ────────────────────────────────

async def _probe_tcp(host: str, port: int) -> bool:
    try:
        fut = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(
            fut, timeout=config.PROBE_TIMEOUT_SECONDS,
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        return True
    except (asyncio.TimeoutError, OSError):
        return False


# ─── ICMP probe (devices) ─────────────────────────────────────────────────

_PING_BIN = shutil.which("ping")
_PING_IS_BSD = platform.system() in ("Darwin", "FreeBSD")


async def _probe_icmp(host: str) -> bool:
    """Single ICMP ping via /usr/bin/ping. Returns True on reply.

    Falls back to TCP-22 if ping is unavailable (rare, but we'd rather
    have something than nothing in that case).
    """
    if not host:
        return False

    if _PING_BIN is None:
        return await _probe_tcp(host, 22)

    if _PING_IS_BSD:
        # BSD ping: -c count, -t timeout (seconds)
        args = [_PING_BIN, "-c", "1", "-t", str(int(config.PROBE_TIMEOUT_SECONDS)), host]
    else:
        # Linux iputils-ping: -c count, -W timeout (seconds, integer)
        args = [_PING_BIN, "-c", "1", "-W", str(int(config.PROBE_TIMEOUT_SECONDS)), host]

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        # Add a small grace period over the ping timeout so we don't kill
        # ping just as it would have produced a reply.
        try:
            rc = await asyncio.wait_for(
                proc.wait(),
                timeout=config.PROBE_TIMEOUT_SECONDS + 2,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return False
        return rc == 0
    except (OSError, FileNotFoundError):
        # Anything wrong with the ping binary → fall back to TCP.
        return await _probe_tcp(host, 22)


# ─── Service single-run probes ────────────────────────────────────────────

async def _run_service_external(client: httpx.AsyncClient, url: str) -> bool:
    """Run SERVICE_PINGS_PER_RUN HTTP probes. True if at least one succeeds."""
    for _ in range(config.SERVICE_PINGS_PER_RUN):
        if await _probe_http(client, url):
            return True
        await asyncio.sleep(0.4)
    return False


async def _run_service_internal(host: str, port: int) -> bool:
    for _ in range(config.SERVICE_PINGS_PER_RUN):
        if await _probe_tcp(host, port):
            return True
        await asyncio.sleep(0.4)
    return False


# ─── Main monitor loop ───────────────────────────────────────────────────

class Monitor:
    """Single periodic task that probes every device + service."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._kick = asyncio.Event()
        self.last_run_at: float | None = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="m-status-monitor")

    async def stop(self) -> None:
        self._stop.set()
        self._kick.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    def kick(self) -> None:
        """Trigger an immediate run (e.g. after a service was added)."""
        self._kick.set()

    async def _loop(self) -> None:
        # Small delay so the DB and overseer have a moment to settle.
        await asyncio.sleep(2)
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:  # noqa: BLE001
                log.exception("Monitor tick failed")
            self.last_run_at = time.time()
            self._kick.clear()
            try:
                await asyncio.wait_for(
                    self._kick.wait(), timeout=config.CHECK_INTERVAL_SECONDS,
                )
            except asyncio.TimeoutError:
                pass

    async def _tick(self) -> None:
        # Run devices and services in parallel — they don't share resources.
        await asyncio.gather(
            self._check_all_devices(),
            self._check_all_services(),
            return_exceptions=False,
        )

    # ── devices ───────────────────────────────────────────────────────────

    async def _check_all_devices(self) -> None:
        async with db.db().execute(
            "SELECT client_id, ip, hostname, consecutive_fail_runs "
            "FROM devices WHERE deleted=0"
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        if not rows:
            return

        # Probe in parallel but cap concurrency so we don't fork 200 pings.
        sem = asyncio.Semaphore(20)

        async def one(d: dict) -> None:
            async with sem:
                await self._check_device(d)

        await asyncio.gather(*(one(d) for d in rows))

    async def _check_device(self, d: dict) -> None:
        # Prefer IP, fall back to hostname.
        target = (d.get("ip") or d.get("hostname") or "").strip()
        prev_fails = int(d.get("consecutive_fail_runs") or 0)
        now = time.time()

        if not target:
            # No address to probe — count as a fail so the user sees it.
            ok = False
        else:
            ok = await _probe_icmp(target)

        new_fails = 0 if ok else prev_fails + 1
        is_online = ok and new_fails == 0

        await db.db().execute(
            "UPDATE devices SET online=?, consecutive_fail_runs=?, last_check=?, "
            "last_seen=COALESCE(?, last_seen), "
            "last_offline_at=CASE WHEN ? THEN NULL ELSE COALESCE(last_offline_at, ?) END "
            "WHERE client_id=?",
            (1 if is_online else 0, new_fails, now,
             now if ok else None,
             1 if ok else 0, now,
             d["client_id"]),
        )
        await db.db().commit()

        await status.record_device_status(
            d["client_id"], status.colour_from_fails(new_fails),
        )

    # ── services ──────────────────────────────────────────────────────────

    async def _check_all_services(self) -> None:
        async with db.db().execute("SELECT * FROM services ORDER BY id") as cur:
            services = [dict(r) for r in await cur.fetchall()]
        if not services:
            return

        async with httpx.AsyncClient(verify=True, http2=False) as http:
            sem = asyncio.Semaphore(10)

            async def one(s: dict) -> None:
                async with sem:
                    await self._check_service(http, s)

            await asyncio.gather(*(one(s) for s in services))

    async def _check_service(self, http: httpx.AsyncClient, s: dict[str, Any]) -> None:
        prev_fails = int(s.get("consecutive_fail_runs") or 0)
        now = time.time()

        if s["is_local"]:
            # Local service: probe ONLY the internal endpoint. Skipping the
            # public URL avoids the Cloudflare-Tunnel-is-up-but-origin-is-down
            # false-green problem.
            if not (s["internal_host"] and s["internal_port"]):
                ok = False  # misconfigured
            else:
                ok = await _run_service_internal(
                    s["internal_host"], int(s["internal_port"])
                )
        else:
            # External service: just hit the URL.
            url = _normalise_url(s["external_url"])
            ok = await _run_service_external(http, url) if url else False

        new_fails = 0 if ok else prev_fails + 1
        live_colour = status.colour_from_fails(new_fails)

        await db.db().execute(
            "UPDATE services SET online=?, consecutive_fail_runs=?, last_check=?, "
            "last_status=? WHERE id=?",
            (1 if ok else 0, new_fails, now, live_colour, s["id"]),
        )
        await db.db().commit()

        await status.record_service_status(int(s["id"]), live_colour)


# Module-level singleton.
monitor = Monitor()
