"""Admin-only API.

Login establishes a cookie session; everything else requires it.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from . import auth, db, monitor, overseer
from .routes_setup import normalise_host, normalise_port

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ─── auth endpoints (no dep) ──────────────────────────────────────────────

class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(body: LoginBody, response: Response):
    admin_id = await auth.authenticate(body.username, body.password)
    if not admin_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Falscher Benutzer oder Passwort")
    token = await db.session_create(admin_id)
    response.set_cookie(
        auth.COOKIE_NAME, token,
        httponly=True, samesite="lax",
        max_age=60 * 60 * 24 * 7,
    )
    return {"ok": True, "username": body.username}


@router.post("/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get(auth.COOKIE_NAME, "")
    await db.session_destroy(token)
    response.delete_cookie(auth.COOKIE_NAME)
    return {"ok": True}


@router.get("/me")
async def me(admin: dict = Depends(auth.current_admin)):
    return {"username": admin["username"]}


# ─── settings ─────────────────────────────────────────────────────────────

class PasswordBody(BaseModel):
    current: str
    new: str = Field(..., min_length=8)


@router.post("/password")
async def change_password(body: PasswordBody, admin: dict = Depends(auth.current_admin)):
    ok = await auth.authenticate(admin["username"], body.current)
    if not ok:
        raise HTTPException(401, "Aktuelles Passwort ist falsch")
    try:
        await auth.change_password(admin["admin_id"], body.new)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


class OverseerBody(BaseModel):
    overseer_host: str | None = None
    overseer_port: int | None = None
    api_key:       str | None = None


@router.post("/overseer")
async def update_overseer(body: OverseerBody, admin: dict = Depends(auth.current_admin)):
    if body.overseer_host is not None:
        raw = body.overseer_host.strip()
        if raw:
            try:
                host = normalise_host(raw)
            except ValueError as e:
                raise HTTPException(400, str(e))
            await db.kv_set("overseer_host", host)
        else:
            await db.kv_set("overseer_host", "")
    if body.overseer_port is not None:
        if body.overseer_port == 0:
            await db.kv_set("overseer_port", "")
        else:
            try:
                port = normalise_port(body.overseer_port)
            except ValueError as e:
                raise HTTPException(400, str(e))
            await db.kv_set("overseer_port", str(port))
    if body.api_key is not None:
        await db.kv_set("overseer_api_key", body.api_key.strip())
    await overseer.client.restart()
    return {"ok": True}


@router.get("/overseer")
async def get_overseer(admin: dict = Depends(auth.current_admin)):
    host, port, key = await db.get_overseer_config()
    return {
        "overseer_host": host or "",
        "overseer_port": port or 0,
        "api_key_set":   bool(key),
        "connection":    overseer.client.status(),
    }


@router.post("/overseer/sync-now")
async def overseer_sync_now(admin: dict = Depends(auth.current_admin)):
    """Force an immediate inventory sync (admin button)."""
    ok, msg, n_seen, n_removed = await overseer.sync_inventory_now()
    if not ok:
        raise HTTPException(400, msg)
    monitor.monitor.kick()  # re-probe right after the inventory update
    return {"ok": True, "seen": n_seen, "removed": n_removed}


class BrandingBody(BaseModel):
    page_subtitle: str | None = None
    manage_label:  str | None = None
    footer_owner:  str | None = None


@router.post("/branding")
async def set_branding(body: BrandingBody, admin: dict = Depends(auth.current_admin)):
    for k, v in body.model_dump().items():
        if v is not None:
            await db.kv_set(k, v.strip())
    return {"ok": True}


@router.get("/branding")
async def get_branding(admin: dict = Depends(auth.current_admin)):
    return await db.get_branding()


# ─── devices ──────────────────────────────────────────────────────────────

@router.get("/devices")
async def list_devices(admin: dict = Depends(auth.current_admin)):
    async with db.db().execute(
        "SELECT d.*, c.name AS category_name FROM devices d "
        "LEFT JOIN categories c ON c.id=d.category_id "
        "WHERE d.deleted=0 ORDER BY d.sort_order, d.name"
    ) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    return {"devices": rows}


class DevicePatch(BaseModel):
    name:        str | None = None
    hidden:      bool | None = None
    category_id: int | None = None
    sort_order:  int | None = None


@router.patch("/devices/{client_id}")
async def patch_device(client_id: str, body: DevicePatch,
                        admin: dict = Depends(auth.current_admin)):
    fields, values = [], []
    if body.name is not None and body.name.strip():
        fields.append("name=?"); values.append(body.name.strip())
    if body.hidden is not None:
        fields.append("hidden=?"); values.append(1 if body.hidden else 0)
    if body.category_id is not None:
        fields.append("category_id=?")
        values.append(body.category_id if body.category_id > 0 else None)
    if body.sort_order is not None:
        fields.append("sort_order=?"); values.append(int(body.sort_order))
    if not fields:
        return {"ok": True, "noop": True}
    values.append(client_id)
    await db.db().execute(
        f"UPDATE devices SET {', '.join(fields)} WHERE client_id=?", values,
    )
    await db.db().commit()
    return {"ok": True}


@router.delete("/devices/{client_id}")
async def remove_device(client_id: str, admin: dict = Depends(auth.current_admin)):
    """Soft-delete from M-STATUS (history is kept)."""
    await db.db().execute("UPDATE devices SET deleted=1 WHERE client_id=?", (client_id,))
    await db.db().commit()
    return {"ok": True}


class DeviceReorderBody(BaseModel):
    order: list[str]  # client_ids in the new order


@router.post("/devices/reorder")
async def reorder_devices(body: DeviceReorderBody,
                           admin: dict = Depends(auth.current_admin)):
    """Bulk sort_order update from the drag-and-drop UI."""
    for idx, cid in enumerate(body.order):
        await db.db().execute(
            "UPDATE devices SET sort_order=? WHERE client_id=?",
            (idx, cid),
        )
    await db.db().commit()
    return {"ok": True, "n": len(body.order)}


# ─── categories ───────────────────────────────────────────────────────────

class CategoryBody(BaseModel):
    name:       str = Field(..., min_length=1, max_length=64)
    sort_order: int = 0


@router.get("/categories")
async def list_categories(admin: dict = Depends(auth.current_admin)):
    async with db.db().execute(
        "SELECT * FROM categories ORDER BY sort_order, name"
    ) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    return {"categories": rows}


@router.post("/categories")
async def create_category(body: CategoryBody, admin: dict = Depends(auth.current_admin)):
    try:
        async with db.db().execute(
            "INSERT INTO categories(name, sort_order, created_at) VALUES(?,?,?)",
            (body.name.strip(), body.sort_order, time.time()),
        ) as cur:
            await db.db().commit()
            cid = cur.lastrowid
    except Exception as e:  # noqa: BLE001 — catches UNIQUE violation
        raise HTTPException(400, f"Kategorie konnte nicht angelegt werden: {e}")
    return {"id": cid, "ok": True}


@router.patch("/categories/{cat_id}")
async def patch_category(cat_id: int, body: CategoryBody,
                          admin: dict = Depends(auth.current_admin)):
    await db.db().execute(
        "UPDATE categories SET name=?, sort_order=? WHERE id=?",
        (body.name.strip(), body.sort_order, cat_id),
    )
    await db.db().commit()
    return {"ok": True}


@router.delete("/categories/{cat_id}")
async def delete_category(cat_id: int, admin: dict = Depends(auth.current_admin)):
    await db.db().execute("DELETE FROM categories WHERE id=?", (cat_id,))
    await db.db().commit()
    return {"ok": True}


# ─── services ─────────────────────────────────────────────────────────────

class ServiceBody(BaseModel):
    name:          str = Field(..., min_length=1, max_length=128)
    external_url:  str = Field(..., min_length=1)
    is_local:      bool = False
    internal_host: str | None = None
    internal_port: int | None = None
    category_id:   int | None = None
    sort_order:    int = 0


def _validate_service(b: ServiceBody) -> None:
    if b.is_local:
        if not b.internal_host or not b.internal_port:
            raise HTTPException(400, "Lokale Services brauchen interne IP und Port")
        if not (1 <= b.internal_port <= 65535):
            raise HTTPException(400, "Port muss zwischen 1 und 65535 liegen")


@router.get("/services")
async def list_services(admin: dict = Depends(auth.current_admin)):
    async with db.db().execute(
        "SELECT s.*, c.name AS category_name FROM services s "
        "LEFT JOIN categories c ON c.id=s.category_id "
        "ORDER BY s.sort_order, s.name"
    ) as cur:
        return {"services": [dict(r) for r in await cur.fetchall()]}


@router.post("/services")
async def create_service(body: ServiceBody, admin: dict = Depends(auth.current_admin)):
    _validate_service(body)
    cat_id = body.category_id if body.category_id and body.category_id > 0 else None
    async with db.db().execute(
        "INSERT INTO services(name, external_url, is_local, internal_host, "
        "internal_port, category_id, sort_order, created_at) VALUES(?,?,?,?,?,?,?,?)",
        (body.name.strip(), body.external_url.strip(),
         1 if body.is_local else 0,
         body.internal_host.strip() if body.internal_host else None,
         body.internal_port,
         cat_id,
         body.sort_order, time.time()),
    ) as cur:
        await db.db().commit()
        sid = cur.lastrowid
    monitor.monitor.kick()
    return {"id": sid, "ok": True}


@router.patch("/services/{sid}")
async def patch_service(sid: int, body: ServiceBody,
                         admin: dict = Depends(auth.current_admin)):
    _validate_service(body)
    cat_id = body.category_id if body.category_id and body.category_id > 0 else None
    await db.db().execute(
        "UPDATE services SET name=?, external_url=?, is_local=?, internal_host=?, "
        "internal_port=?, category_id=?, sort_order=? WHERE id=?",
        (body.name.strip(), body.external_url.strip(),
         1 if body.is_local else 0,
         body.internal_host.strip() if body.internal_host else None,
         body.internal_port,
         cat_id,
         body.sort_order, sid),
    )
    await db.db().commit()
    monitor.monitor.kick()
    return {"ok": True}


@router.delete("/services/{sid}")
async def delete_service(sid: int, admin: dict = Depends(auth.current_admin)):
    await db.db().execute("DELETE FROM services WHERE id=?", (sid,))
    await db.db().commit()
    return {"ok": True}


class ServiceReorderBody(BaseModel):
    order: list[int]


@router.post("/services/reorder")
async def reorder_services(body: ServiceReorderBody,
                            admin: dict = Depends(auth.current_admin)):
    for idx, sid in enumerate(body.order):
        await db.db().execute(
            "UPDATE services SET sort_order=? WHERE id=?",
            (idx, sid),
        )
    await db.db().commit()
    return {"ok": True, "n": len(body.order)}


# ─── monitor controls ─────────────────────────────────────────────────────

@router.post("/services/check-now")
async def trigger_check(admin: dict = Depends(auth.current_admin)):
    monitor.monitor.kick()
    return {"ok": True}
