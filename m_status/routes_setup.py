"""First-boot setup wizard.

Locked once setup_complete=1. Validates the overseer key by issuing a
GET /api/plugin/devices against the supplied host:port.
"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException, Response, status as http_status
from pydantic import BaseModel, Field

from . import auth, db, overseer

router = APIRouter(prefix="/api/setup", tags=["setup"])
log = logging.getLogger("m_status.setup")


# ─── helpers ──────────────────────────────────────────────────────────────

_HOST_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def normalise_host(raw: str) -> str:
    """Accept IPs and hostnames, reject schemes/paths/whitespace."""
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("Host darf nicht leer sein")
    # Strip a scheme if the user pasted one anyway, and any path.
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    raw = raw.split("/", 1)[0]
    # Strip a trailing :port if the user squeezed both into the host field.
    if ":" in raw and raw.count(":") == 1:
        raw = raw.split(":", 1)[0]
    if not _HOST_RE.match(raw):
        raise ValueError(f"Ungültiger Host: {raw!r}")
    return raw


def normalise_port(raw) -> int:
    try:
        p = int(raw)
    except (TypeError, ValueError):
        raise ValueError("Port muss eine Zahl sein")
    if not (1 <= p <= 65535):
        raise ValueError("Port muss zwischen 1 und 65535 liegen")
    return p


# ─── request bodies ───────────────────────────────────────────────────────

class SetupBody(BaseModel):
    # Overseer is optional — leaving it empty means "standalone mode".
    overseer_host: str | None = None
    overseer_port: int | None = None
    api_key:       str | None = None
    admin_user:    str = Field(..., min_length=1, max_length=64)
    admin_pass:    str = Field(..., min_length=8)
    page_subtitle: str | None = None


class TestBody(BaseModel):
    overseer_host: str
    overseer_port: int
    api_key:       str


# ─── routes ───────────────────────────────────────────────────────────────

@router.get("/state")
async def setup_state():
    return {"setup_complete": await db.is_setup_complete()}


@router.post("/test")
async def setup_test(body: TestBody):
    if await db.is_setup_complete():
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, "Setup ist bereits abgeschlossen")
    try:
        host = normalise_host(body.overseer_host)
        port = normalise_port(body.overseer_port)
    except ValueError as e:
        raise HTTPException(400, str(e))
    ok, msg, _ = await overseer.fetch_inventory(host, port, body.api_key)
    return {"ok": ok, "message": msg}


@router.post("/complete")
async def setup_complete(body: SetupBody, response: Response):
    if await db.is_setup_complete():
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, "Setup ist bereits abgeschlossen")

    raw_host = (body.overseer_host or "").strip()
    raw_port = body.overseer_port
    raw_key = (body.api_key or "").strip()
    has_overseer = bool(raw_host and raw_port and raw_key)

    if has_overseer:
        try:
            host = normalise_host(raw_host)
            port = normalise_port(raw_port)
        except ValueError as e:
            raise HTTPException(400, str(e))

        ok, msg, _ = await overseer.fetch_inventory(host, port, raw_key)
        if not ok:
            raise HTTPException(400, f"Overseer-Test fehlgeschlagen: {msg}")

        await db.set_overseer_config(host, port, raw_key)
    else:
        # Standalone: clear any stray credentials.
        await db.set_overseer_config("", 0, "")

    if body.page_subtitle:
        await db.kv_set("page_subtitle", body.page_subtitle.strip())

    # Create the admin user.
    try:
        admin_id = await auth.create_admin(body.admin_user, body.admin_pass)
    except ValueError as e:
        raise HTTPException(400, str(e))

    await db.kv_set("setup_complete", "1")

    # Auto-login the admin so they land on /manage if they want.
    token = await db.session_create(admin_id)
    response.set_cookie(auth.COOKIE_NAME, token, httponly=True, samesite="lax")

    # Kick the overseer client so it picks up the new credentials.
    await overseer.client.restart()

    return {"ok": True, "standalone": not has_overseer}
