"""Admin authentication for M-STATUS.

bcrypt password hashing, cookie-bound sessions persisted in SQLite.
"""
from __future__ import annotations

import time
from typing import Any

import bcrypt
from fastapi import HTTPException, Request, status

from . import db

COOKIE_NAME = "m_status_session"


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


async def create_admin(username: str, password: str) -> int:
    username = username.strip()
    if not username:
        raise ValueError("username darf nicht leer sein")
    if len(password) < 8:
        raise ValueError("Passwort muss mindestens 8 Zeichen haben")
    pw_hash = hash_password(password)
    async with db.db().execute(
        "INSERT INTO admins(username, password_hash, created_at) VALUES(?,?,?)",
        (username, pw_hash, time.time()),
    ) as cur:
        await db.db().commit()
        return cur.lastrowid or 0


async def authenticate(username: str, password: str) -> int | None:
    async with db.db().execute(
        "SELECT id, password_hash FROM admins WHERE username=?", (username.strip(),)
    ) as cur:
        row = await cur.fetchone()
    if row and verify_password(password, row["password_hash"]):
        return int(row["id"])
    return None


async def change_password(admin_id: int, new_password: str) -> None:
    if len(new_password) < 8:
        raise ValueError("Passwort muss mindestens 8 Zeichen haben")
    await db.db().execute(
        "UPDATE admins SET password_hash=? WHERE id=?",
        (hash_password(new_password), admin_id),
    )
    await db.db().commit()


async def get_admin(admin_id: int) -> dict[str, Any] | None:
    async with db.db().execute(
        "SELECT id, username, created_at FROM admins WHERE id=?", (admin_id,)
    ) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


# ─── FastAPI deps ─────────────────────────────────────────────────────────

async def current_admin(request: Request) -> dict[str, Any]:
    """FastAPI dep: requires a logged-in admin or raises 401."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nicht eingeloggt")
    sess = await db.session_lookup(token)
    if not sess:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session abgelaufen")
    return sess


async def optional_admin(request: Request) -> dict[str, Any] | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    return await db.session_lookup(token)
