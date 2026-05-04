"""Status calculation for devices and services.

Day slots are UTC-based and store the *worst* status seen on that day:
red > orange > green. Rollovers happen automatically when events arrive
on a new day.

Devices are *actively probed* by M-STATUS itself (see monitor.py). The
overseer is consulted only for the device inventory (names, IPs) — its
online/offline events are advisory. Same fail-counter scheme as services:

    0 fails              → green   (Online)
    1..DEGRADED_AFTER-1  → orange  (Probleme)
    DEGRADED..OFFLINE-1  → red     ("Fehlerhaft")
    ≥ OFFLINE_AFTER      → red     ("Offline")

The UI label distinguishes the two red sub-states; the day-history slot
just records `red` for both.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import aiosqlite

from . import config, db


# ─── status constants ─────────────────────────────────────────────────────

GREEN = "green"
ORANGE = "orange"
RED = "red"
GREY = "grey"  # only used in API responses (no data), never stored

# UI-level labels for entities (devices + services share these).
LABEL_ONLINE     = "Online"
LABEL_PROBLEMS   = "Probleme"
LABEL_DEGRADED   = "Fehlerhaft"
LABEL_OFFLINE    = "Offline"
LABEL_NO_DATA    = "Keine Daten"

_RANK = {GREEN: 0, ORANGE: 1, RED: 2}


def worst(*statuses: str) -> str:
    """Return the worst status given (red > orange > green)."""
    valid = [s for s in statuses if s in _RANK]
    if not valid:
        return GREEN
    return max(valid, key=lambda s: _RANK[s])


def today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def day_keys_window(days: int) -> list[str]:
    """Returns the last `days` day keys, oldest first."""
    today = datetime.now(timezone.utc).date()
    return [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]


# ─── colour + label from fail-count (the canonical state machine) ─────────

def colour_from_fails(fails: int) -> str:
    """Day-strip / banner colour for a given consecutive_fail_runs value."""
    if fails <= 0:
        return GREEN
    if fails < config.DEGRADED_AFTER_FAILS:
        return ORANGE
    return RED


def label_from_fails(fails: int) -> str:
    """Human-readable label for the *current* state."""
    if fails <= 0:
        return LABEL_ONLINE
    if fails < config.DEGRADED_AFTER_FAILS:
        return LABEL_PROBLEMS
    if fails < config.OFFLINE_AFTER_FAILS:
        return LABEL_DEGRADED
    return LABEL_OFFLINE


# ─── history writers ──────────────────────────────────────────────────────

async def _bump_history(table: str, id_col: str, entity_id, day: str, status: str) -> None:
    """Set day-slot to `status` if it's worse than what's stored."""
    async with db.db().execute(
        f"SELECT status FROM {table} WHERE {id_col}=? AND day=?",
        (entity_id, day),
    ) as cur:
        existing = await cur.fetchone()
    if existing is None:
        await db.db().execute(
            f"INSERT INTO {table}({id_col}, day, status) VALUES(?,?,?)",
            (entity_id, day, status),
        )
    else:
        new_status = worst(existing["status"], status)
        if new_status != existing["status"]:
            await db.db().execute(
                f"UPDATE {table} SET status=? WHERE {id_col}=? AND day=?",
                (new_status, entity_id, day),
            )
    await db.db().commit()


async def record_device_status(client_id: str, status: str, day: str | None = None) -> None:
    await _bump_history("device_history", "device_id", client_id, day or today_key(), status)


async def record_service_status(service_id: int, status: str, day: str | None = None) -> None:
    await _bump_history("service_history", "service_id", service_id, day or today_key(), status)


# ─── device inventory updates (overseer → local) ─────────────────────────

async def upsert_device_meta(meta: dict) -> None:
    """Insert/update a device record from overseer metadata.

    Only touches the fields we trust the overseer for (name, hostname, os,
    platform, ip). Online status is *not* updated here — that's the job of
    the active probe in monitor.py.
    """
    cid = meta.get("client_id")
    if not cid:
        return
    now = time.time()
    name = meta.get("client_name") or meta.get("hostname") or cid

    async with db.db().execute(
        "SELECT client_id FROM devices WHERE client_id=?", (cid,)
    ) as cur:
        row = await cur.fetchone()

    if row is None:
        await db.db().execute(
            "INSERT INTO devices(client_id, name, hostname, os, platform, ip, "
            "online, last_seen, first_seen) VALUES(?,?,?,?,?,?,0,?,?)",
            (cid, name, meta.get("hostname"), meta.get("os"),
             meta.get("platform"), meta.get("ip"), meta.get("last_seen"), now),
        )
    else:
        # Don't clobber a user-edited name. Hostname/os/platform/ip are
        # owned by the overseer and always overwritten.
        await db.db().execute(
            "UPDATE devices SET hostname=?, os=?, platform=?, ip=?, deleted=0 "
            "WHERE client_id=?",
            (meta.get("hostname"), meta.get("os"), meta.get("platform"),
             meta.get("ip"), cid),
        )
    await db.db().commit()


async def reconcile_with_overseer(known_client_ids: set[str]) -> int:
    """Soft-delete devices that the overseer no longer knows about.

    Returns the number of devices marked as deleted.
    """
    if not known_client_ids:
        # Empty inventory could mean overseer had a bad day — refuse to wipe.
        return 0
    placeholders = ",".join(["?"] * len(known_client_ids))
    async with db.db().execute(
        f"SELECT client_id FROM devices "
        f"WHERE deleted=0 AND client_id NOT IN ({placeholders})",
        tuple(known_client_ids),
    ) as cur:
        rows = await cur.fetchall()
    removed = [r["client_id"] for r in rows]
    if removed:
        await db.db().execute(
            f"UPDATE devices SET deleted=1, online=0 "
            f"WHERE client_id IN ({','.join(['?'] * len(removed))})",
            tuple(removed),
        )
        await db.db().commit()
    return len(removed)


async def device_deleted(client_id: str) -> None:
    """Soft-delete a single device. History is preserved."""
    await db.db().execute(
        "UPDATE devices SET deleted=1, online=0 WHERE client_id=?",
        (client_id,),
    )
    await db.db().commit()


# ─── live colour + label ──────────────────────────────────────────────────

def device_live_colour(row: aiosqlite.Row | dict) -> str:
    if isinstance(row, aiosqlite.Row):
        d = dict(row)
    else:
        d = row
    if d.get("deleted"):
        return GREY
    fails = int(d.get("consecutive_fail_runs") or 0)
    if fails == 0 and d.get("last_check") is None:
        # Never probed yet — show grey instead of a misleading "online".
        return GREY
    return colour_from_fails(fails)


def device_live_label(row: aiosqlite.Row | dict) -> str:
    if isinstance(row, aiosqlite.Row):
        d = dict(row)
    else:
        d = row
    if d.get("deleted"):
        return LABEL_NO_DATA
    if d.get("last_check") is None and not int(d.get("consecutive_fail_runs") or 0):
        return LABEL_NO_DATA
    return label_from_fails(int(d.get("consecutive_fail_runs") or 0))


def service_live_colour(row: aiosqlite.Row | dict) -> str:
    if isinstance(row, aiosqlite.Row):
        d = dict(row)
    else:
        d = row
    if d.get("last_check") is None:
        return GREY
    return colour_from_fails(int(d.get("consecutive_fail_runs") or 0))


def service_live_label(row: aiosqlite.Row | dict) -> str:
    if isinstance(row, aiosqlite.Row):
        d = dict(row)
    else:
        d = row
    if d.get("last_check") is None:
        return LABEL_NO_DATA
    return label_from_fails(int(d.get("consecutive_fail_runs") or 0))


# ─── history maintenance ──────────────────────────────────────────────────

async def prune_old_history() -> None:
    """Drop history older than HISTORY_DAYS."""
    cutoff = (datetime.now(timezone.utc).date()
              - timedelta(days=config.HISTORY_DAYS - 1)).strftime("%Y-%m-%d")
    await db.db().execute("DELETE FROM device_history WHERE day < ?", (cutoff,))
    await db.db().execute("DELETE FROM service_history WHERE day < ?", (cutoff,))
    await db.db().commit()


async def get_device_strip(client_id: str) -> list[dict]:
    """Return a list of {day, status} for the last HISTORY_DAYS, oldest first.

    Days with no data are returned with status='grey'.
    """
    days = day_keys_window(config.HISTORY_DAYS)
    async with db.db().execute(
        "SELECT day, status FROM device_history "
        "WHERE device_id=? AND day >= ? ORDER BY day",
        (client_id, days[0]),
    ) as cur:
        rows = {r["day"]: r["status"] for r in await cur.fetchall()}
    return [{"day": d, "status": rows.get(d, GREY)} for d in days]


async def get_service_strip(service_id: int) -> list[dict]:
    days = day_keys_window(config.HISTORY_DAYS)
    async with db.db().execute(
        "SELECT day, status FROM service_history "
        "WHERE service_id=? AND day >= ? ORDER BY day",
        (service_id, days[0]),
    ) as cur:
        rows = {r["day"]: r["status"] for r in await cur.fetchall()}
    return [{"day": d, "status": rows.get(d, GREY)} for d in days]


def uptime_percent(strip: list[dict]) -> float:
    """Weighted uptime: green=1.0, orange=0.5, red/grey=0.

    Grey days don't count against you (they're "no data"), so we average
    only over the days that have a real reading.
    """
    real = [s for s in strip if s["status"] in (GREEN, ORANGE, RED)]
    if not real:
        return 100.0
    score = 0.0
    for s in real:
        if s["status"] == GREEN:
            score += 1.0
        elif s["status"] == ORANGE:
            score += 0.5
    return round(score / len(real) * 100, 2)
