"""M-OBSERVE overseer integration.

Two channels working together:

* **REST sync** (`GET /api/plugin/devices`): periodic source-of-truth for the
  device inventory. Runs once at startup, then every DEVICE_SYNC_INTERVAL.
  Devices that the overseer no longer knows about are soft-deleted locally.
  This is what we trust for *which devices exist* and their *names + IPs*.

* **WebSocket** (`/ws/plugin`): best-effort live channel. We listen for
  `device_online`, `device_offline`, `device_deleted` events to update the
  inventory faster than the daily REST poll would. We do **not** trust the
  online/offline flag for the device's actual reachability — that's what
  M-STATUS's own ICMP probe is for (see monitor.py).

If the WS is unavailable, M-STATUS still works correctly via REST sync alone.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx
import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus

from . import config, db, status

log = logging.getLogger("m_status.overseer")


# ─── url builders ─────────────────────────────────────────────────────────

def _http_base(host: str, port: int) -> str:
    return f"http://{host}:{int(port)}"


def _ws_url(host: str, port: int) -> str:
    return f"ws://{host}:{int(port)}/ws/plugin"


# ─── REST sync ────────────────────────────────────────────────────────────

async def fetch_inventory(host: str, port: int, api_key: str,
                          timeout: float = 10.0) -> tuple[bool, str, list[dict]]:
    """One-shot REST snapshot. Returns (ok, message, devices)."""
    url = f"{_http_base(host, port)}/api/plugin/devices"
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(url, headers={"X-API-Key": api_key})
        if r.status_code == 401:
            return False, "API-Key wurde vom Overseer abgelehnt", []
        if r.status_code == 404:
            return False, ("Endpoint /api/plugin/devices nicht gefunden — "
                          "ist das Plugin-Protocol auf dem Overseer aktiviert?"), []
        if r.status_code >= 500:
            return False, f"Overseer Server-Fehler ({r.status_code})", []
        if r.status_code != 200:
            return False, f"Unerwarteter HTTP-Status {r.status_code}", []
        try:
            data = r.json()
        except ValueError:
            return False, "Antwort ist kein gültiges JSON", []
        devices = data.get("devices") or []
        if not isinstance(devices, list):
            return False, "Antwort enthält kein devices-Array", []
        return True, "OK", devices
    except httpx.HTTPError as e:
        return False, f"Verbindung fehlgeschlagen: {e}", []


async def sync_inventory_now() -> tuple[bool, str, int, int]:
    """Pull the inventory from the configured overseer and reconcile it.

    Returns (ok, message, upserted, removed).
    """
    host, port, key = await db.get_overseer_config()
    if not host or not port or not key:
        return False, "Overseer nicht konfiguriert", 0, 0

    ok, msg, devs = await fetch_inventory(host, port, key)
    if not ok:
        return False, msg, 0, 0

    seen: set[str] = set()
    for d in devs:
        cid = d.get("client_id")
        if not cid:
            continue
        seen.add(cid)
        await status.upsert_device_meta(d)

    removed = await status.reconcile_with_overseer(seen)
    return True, "OK", len(seen), removed


# ─── WebSocket consumer ───────────────────────────────────────────────────

class OverseerClient:
    """Long-running WS consumer + on-demand REST sync."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.connected: bool = False
        self.last_error: str | None = None
        self.last_connect_at: float | None = None
        self.last_disconnect_at: float | None = None
        self.last_sync_at: float | None = None
        self.last_sync_ok: bool = False

    # Public ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="overseer-ws")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def restart(self) -> None:
        """Re-read config and reconnect (call after a settings change)."""
        await self.stop()
        await self.start()

    def status(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "last_error": self.last_error,
            "last_connect_at": self.last_connect_at,
            "last_disconnect_at": self.last_disconnect_at,
            "last_sync_at": self.last_sync_at,
            "last_sync_ok": self.last_sync_ok,
        }

    # Run loop ────────────────────────────────────────────────────────────

    async def _run(self) -> None:
        backoff = config.OVERSEER_RECONNECT_MIN
        while not self._stop.is_set():
            host, port, key = await db.get_overseer_config()
            if not host or not port or not key:
                # Not configured — quietly idle.
                self.connected = False
                self.last_error = "Overseer noch nicht konfiguriert"
                await asyncio.sleep(5)
                continue

            # Always do a REST sync on (re)connect — it's the source of truth.
            ok, msg, n_seen, n_removed = await sync_inventory_now()
            self.last_sync_at = time.time()
            self.last_sync_ok = ok
            if ok:
                log.info("Inventory sync OK: %d devices, %d removed", n_seen, n_removed)
            else:
                log.warning("Inventory sync failed: %s", msg)
                self.last_error = msg
                # No point in trying the WS if REST is broken.
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                    break
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, config.OVERSEER_RECONNECT_MAX)
                continue

            # REST OK → try the WS for live updates. If it fails, that's fine,
            # we'll just rely on periodic REST syncs.
            try:
                log.info("Connecting to overseer WS at %s", _ws_url(host, port))
                async with websockets.connect(
                    _ws_url(host, port), open_timeout=10, ping_interval=30,
                ) as ws:
                    await self._authenticate(ws, key)
                    self.connected = True
                    self.last_connect_at = time.time()
                    self.last_error = None
                    backoff = config.OVERSEER_RECONNECT_MIN
                    await self._consume(ws)
            except asyncio.CancelledError:
                raise
            except (ConnectionClosed, InvalidStatus, OSError, asyncio.TimeoutError) as e:
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning("Overseer WS issue: %s", self.last_error)
            except Exception as e:  # noqa: BLE001
                self.last_error = f"{type(e).__name__}: {e}"
                log.exception("Overseer WS unexpected error")
            finally:
                if self.connected:
                    self.last_disconnect_at = time.time()
                self.connected = False

            # Backoff before reconnecting.
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                break
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, config.OVERSEER_RECONNECT_MAX)

    async def _authenticate(self, ws, key: str) -> None:
        await ws.send(json.dumps({"api_key": key}))
        first = await asyncio.wait_for(ws.recv(), timeout=15)
        msg = json.loads(first)
        if msg.get("type") != "init":
            raise RuntimeError(f"Unerwartete erste Nachricht: {msg!r}")
        # Treat the init frame as another inventory snapshot — same shape as
        # the REST endpoint, just streamed.
        await self._handle_init(msg)

    async def _consume(self, ws) -> None:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("Bad JSON from overseer: %r", raw[:200])
                continue
            await self._handle(msg)

    # Frame handlers ──────────────────────────────────────────────────────

    async def _handle(self, msg: dict[str, Any]) -> None:
        t = msg.get("type")
        if t == "init":
            await self._handle_init(msg)
        elif t == "device_online":
            # New or returning device — refresh its metadata. The actual
            # reachability is M-STATUS's own job.
            await status.upsert_device_meta(msg)
        elif t == "device_offline":
            # We don't trust this as ground truth. Ignore.
            pass
        elif t == "device_deleted":
            cid = msg.get("client_id")
            if cid:
                await status.device_deleted(cid)
        else:
            log.debug("Ignoring unknown frame type=%s", t)

    async def _handle_init(self, msg: dict[str, Any]) -> None:
        devices = msg.get("devices") or []
        log.info("WS init received: %d devices", len(devices))
        seen: set[str] = set()
        for d in devices:
            cid = d.get("client_id")
            if not cid:
                continue
            seen.add(cid)
            await status.upsert_device_meta(d)
        # Reconcile only if the WS init looks complete enough.
        if seen:
            await status.reconcile_with_overseer(seen)


# Module-level singleton.
client = OverseerClient()
