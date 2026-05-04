"""SQLite layer for M-STATUS.

Single connection, async via aiosqlite. Schema is created on startup.
Table names are kept short and obvious; `client_id` mirrors the overseer.

Schema migrations are forward-only and idempotent — see `_migrate()`.
"""
from __future__ import annotations

import logging
import secrets
import time
from typing import Any, Iterable

import aiosqlite

from . import config

log = logging.getLogger("m_status.db")

# Bumped whenever the schema changes in a way that requires a migration.
SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS admins (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    client_id             TEXT PRIMARY KEY,
    name                  TEXT NOT NULL,
    hostname              TEXT,
    os                    TEXT,
    platform              TEXT,
    ip                    TEXT,
    hidden                INTEGER NOT NULL DEFAULT 0,
    category_id           INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    sort_order            INTEGER NOT NULL DEFAULT 0,
    online                INTEGER NOT NULL DEFAULT 0,
    consecutive_fail_runs INTEGER NOT NULL DEFAULT 0,
    last_check            REAL,
    last_seen             REAL,
    last_offline_at       REAL,
    first_seen            REAL NOT NULL,
    deleted               INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS device_history (
    device_id TEXT NOT NULL REFERENCES devices(client_id) ON DELETE CASCADE,
    day       TEXT NOT NULL,           -- YYYY-MM-DD (UTC)
    status    TEXT NOT NULL,           -- 'green' | 'orange' | 'red'
    PRIMARY KEY (device_id, day)
);

CREATE INDEX IF NOT EXISTS idx_device_history_day ON device_history(day);

CREATE TABLE IF NOT EXISTS services (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    name                  TEXT NOT NULL,
    external_url          TEXT NOT NULL,
    is_local              INTEGER NOT NULL DEFAULT 0,
    internal_host         TEXT,
    internal_port         INTEGER,
    category_id           INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    sort_order            INTEGER NOT NULL DEFAULT 0,
    online                INTEGER NOT NULL DEFAULT 1,
    consecutive_fail_runs INTEGER NOT NULL DEFAULT 0,
    last_check            REAL,
    last_status           TEXT,         -- 'green' | 'orange' | 'red'
    created_at            REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS service_history (
    service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    day        TEXT NOT NULL,
    status     TEXT NOT NULL,
    PRIMARY KEY (service_id, day)
);

CREATE INDEX IF NOT EXISTS idx_service_history_day ON service_history(day);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    admin_id   INTEGER NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
"""


# ─── connection management ────────────────────────────────────────────────

_db: aiosqlite.Connection | None = None


async def init() -> aiosqlite.Connection:
    """Open the connection (once), create schema, run migrations, return it."""
    global _db
    if _db is not None:
        return _db
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(config.DB_PATH)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.executescript(SCHEMA)
    await conn.commit()
    _db = conn
    await _migrate(conn)
    return conn


async def close() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None


def db() -> aiosqlite.Connection:
    if _db is None:
        raise RuntimeError("DB not initialised — call init() first")
    return _db


# ─── schema migrations ────────────────────────────────────────────────────

async def _column_exists(conn: aiosqlite.Connection, table: str, col: str) -> bool:
    async with conn.execute(f"PRAGMA table_info({table})") as cur:
        rows = await cur.fetchall()
    return any(r[1] == col for r in rows)


async def _migrate(conn: aiosqlite.Connection) -> None:
    """Forward-only schema migrations.

    Each step is idempotent and safe to run on a fresh DB or an upgraded one.
    The `schema_version` kv key tracks where we are.
    """
    raw = await kv_get("schema_version", "0")
    try:
        current = int(raw or "0")
    except ValueError:
        current = 0

    # v0 → v1: legacy migration safety. Older instances may pre-date the
    # `categories` and `sort_order` columns. Add them if missing.
    if current < 1:
        if not await _column_exists(conn, "devices", "category_id"):
            await conn.execute(
                "ALTER TABLE devices ADD COLUMN category_id INTEGER "
                "REFERENCES categories(id) ON DELETE SET NULL"
            )
        if not await _column_exists(conn, "devices", "sort_order"):
            await conn.execute(
                "ALTER TABLE devices ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0"
            )
        if not await _column_exists(conn, "services", "sort_order"):
            await conn.execute(
                "ALTER TABLE services ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0"
            )
        await conn.commit()
        log.info("Migrated schema → v1 (categories baseline)")
        current = 1

    # v1 → v2: services get a category and devices learn to count their own fails.
    if current < 2:
        if not await _column_exists(conn, "services", "category_id"):
            await conn.execute(
                "ALTER TABLE services ADD COLUMN category_id INTEGER "
                "REFERENCES categories(id) ON DELETE SET NULL"
            )
        if not await _column_exists(conn, "devices", "consecutive_fail_runs"):
            await conn.execute(
                "ALTER TABLE devices ADD COLUMN consecutive_fail_runs "
                "INTEGER NOT NULL DEFAULT 0"
            )
        if not await _column_exists(conn, "devices", "last_check"):
            await conn.execute(
                "ALTER TABLE devices ADD COLUMN last_check REAL"
            )
        # If we used to store the overseer URL as a single value, split it
        # into host+port so the new settings UI shows the right thing.
        legacy_url = await kv_get("overseer_url", "")
        if legacy_url and not await kv_get("overseer_host"):
            from urllib.parse import urlparse
            try:
                p = urlparse(legacy_url if "://" in legacy_url else "http://" + legacy_url)
                if p.hostname:
                    await kv_set("overseer_host", p.hostname)
                if p.port:
                    await kv_set("overseer_port", str(p.port))
            except Exception:  # noqa: BLE001
                log.warning("Could not parse legacy overseer_url=%r", legacy_url)
        await conn.commit()
        log.info("Migrated schema → v2 (services-categories + active device probing)")
        current = 2

    # v2 → v3: branding cleanup. `footer_link`, `footer_label` and `page_title`
    # are now hardcoded in the public template — drop the kv entries so old
    # values don't reappear in the admin panel after upgrade.
    if current < 3:
        for k in ("footer_link", "footer_label", "page_title", "overseer_url"):
            await conn.execute("DELETE FROM kv WHERE key=?", (k,))
        await conn.commit()
        log.info("Migrated schema → v3 (branding cleanup)")
        current = 3

    await kv_set("schema_version", str(SCHEMA_VERSION))


# ─── kv helpers ───────────────────────────────────────────────────────────

async def kv_get(key: str, default: str | None = None) -> str | None:
    async with db().execute("SELECT value FROM kv WHERE key=?", (key,)) as cur:
        row = await cur.fetchone()
    return row["value"] if row else default


async def kv_set(key: str, value: str) -> None:
    await db().execute(
        "INSERT INTO kv(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    await db().commit()


async def kv_delete(key: str) -> None:
    await db().execute("DELETE FROM kv WHERE key=?", (key,))
    await db().commit()


# ─── setup / config helpers ───────────────────────────────────────────────

async def is_setup_complete() -> bool:
    return (await kv_get("setup_complete")) == "1"


async def get_overseer_config() -> tuple[str | None, int | None, str | None]:
    """Returns (host, port, api_key). Host is an IP or hostname; no scheme."""
    host = await kv_get("overseer_host")
    port_raw = await kv_get("overseer_port")
    key = await kv_get("overseer_api_key")
    port: int | None = None
    if port_raw:
        try:
            port = int(port_raw)
        except ValueError:
            port = None
    return host, port, key


async def set_overseer_config(host: str | None, port: int | None, api_key: str | None) -> None:
    """Persist overseer connection settings. Empty strings clear the entry."""
    if host is not None:
        await kv_set("overseer_host", host.strip())
    if port is not None:
        await kv_set("overseer_port", str(int(port)))
    if api_key is not None:
        await kv_set("overseer_api_key", api_key.strip())


async def get_branding() -> dict[str, str]:
    """Editable branding only.

    `page_title`, `footer_link` and `footer_label` are intentionally NOT here —
    they're baked into the public template (MSOL v1.1: edit the HTML if you
    really must, otherwise leave it as the author intended).
    """
    return {
        "page_subtitle": await kv_get("page_subtitle", "System Status") or "System Status",
        "manage_label":  await kv_get("manage_label",  "Manage")        or "Manage",
        "footer_owner":  await kv_get("footer_owner",  "Moritzsoft ©")  or "Moritzsoft ©",
    }


# ─── session helpers ──────────────────────────────────────────────────────

async def session_create(admin_id: int, ttl_seconds: int = 86400 * 7) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    await db().execute(
        "INSERT INTO sessions(token, admin_id, created_at, expires_at) VALUES(?,?,?,?)",
        (token, admin_id, now, now + ttl_seconds),
    )
    await db().commit()
    return token


async def session_lookup(token: str) -> dict[str, Any] | None:
    if not token:
        return None
    async with db().execute(
        "SELECT s.admin_id, s.expires_at, a.username "
        "FROM sessions s JOIN admins a ON a.id=s.admin_id "
        "WHERE s.token=?",
        (token,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    if row["expires_at"] < time.time():
        await db().execute("DELETE FROM sessions WHERE token=?", (token,))
        await db().commit()
        return None
    return {"admin_id": row["admin_id"], "username": row["username"]}


async def session_destroy(token: str) -> None:
    if not token:
        return
    await db().execute("DELETE FROM sessions WHERE token=?", (token,))
    await db().commit()


async def sessions_purge_expired() -> None:
    await db().execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
    await db().commit()


# ─── small util ───────────────────────────────────────────────────────────

def rows_to_dicts(rows: Iterable[aiosqlite.Row]) -> list[dict]:
    return [dict(r) for r in rows]
