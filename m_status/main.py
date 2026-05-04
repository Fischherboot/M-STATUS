"""M-STATUS FastAPI app.

Run with:
    python -m m_status
or:
    uvicorn m_status.main:app --host 0.0.0.0 --port 3502
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import auth, config, db, monitor, overseer, status as status_mod
from .routes_admin import router as admin_router
from .routes_public import router as public_router
from .routes_setup import router as setup_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger("m_status")

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = Jinja2Templates(directory=str(ROOT / "templates"))


# ─── background tasks ─────────────────────────────────────────────────────

async def _periodic_overseer_sync():
    """Pull the overseer inventory every DEVICE_SYNC_INTERVAL_SECONDS."""
    # Wait a bit so the WS init has a chance to run first on cold boot.
    await asyncio.sleep(60)
    while True:
        try:
            host, port, key = await db.get_overseer_config()
            if host and port and key:
                ok, msg, n_seen, n_removed = await overseer.sync_inventory_now()
                if ok:
                    log.info("Periodic sync OK: %d devices, %d removed", n_seen, n_removed)
                else:
                    log.warning("Periodic sync failed: %s", msg)
        except Exception:  # noqa: BLE001
            log.exception("Periodic overseer sync crashed")
        await asyncio.sleep(config.DEVICE_SYNC_INTERVAL_SECONDS)


async def _periodic_history_prune():
    """Drop history beyond HISTORY_DAYS once per hour, also purge dead sessions."""
    while True:
        try:
            await status_mod.prune_old_history()
            await db.sessions_purge_expired()
        except Exception:  # noqa: BLE001
            log.exception("History prune failed")
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("M-STATUS starting up")
    await db.init()
    tasks = [
        asyncio.create_task(_periodic_history_prune(),  name="history-prune"),
        asyncio.create_task(_periodic_overseer_sync(),  name="overseer-sync"),
    ]
    await overseer.client.start()
    await monitor.monitor.start()
    try:
        yield
    finally:
        log.info("M-STATUS shutting down")
        await overseer.client.stop()
        await monitor.monitor.stop()
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        await db.close()


# ─── app factory ──────────────────────────────────────────────────────────

app = FastAPI(title="M-STATUS", version="1.1.0", lifespan=lifespan,
              docs_url=None, redoc_url=None)

app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")

app.include_router(public_router)
app.include_router(setup_router)
app.include_router(admin_router)


# ─── page routes ──────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def page_public(request: Request):
    if not await db.is_setup_complete():
        return RedirectResponse("/setup", status_code=302)
    branding = await db.get_branding()
    return TEMPLATES.TemplateResponse(
        request, "public.html",
        {"branding": branding, "history_days": config.HISTORY_DAYS},
    )


@app.get("/setup", response_class=HTMLResponse)
async def page_setup(request: Request):
    if await db.is_setup_complete():
        return RedirectResponse("/", status_code=302)
    return TEMPLATES.TemplateResponse(
        request, "setup.html", {"branding": await db.get_branding()},
    )


@app.get("/login", response_class=HTMLResponse)
async def page_login(request: Request):
    if not await db.is_setup_complete():
        return RedirectResponse("/setup", status_code=302)
    me = await auth.optional_admin(request)
    if me:
        return RedirectResponse("/manage", status_code=302)
    return TEMPLATES.TemplateResponse(
        request, "login.html", {"branding": await db.get_branding()},
    )


@app.get("/manage", response_class=HTMLResponse)
async def page_manage(request: Request):
    if not await db.is_setup_complete():
        return RedirectResponse("/setup", status_code=302)
    me = await auth.optional_admin(request)
    if not me:
        return RedirectResponse("/login", status_code=302)
    return TEMPLATES.TemplateResponse(
        request, "manage.html",
        {"branding": await db.get_branding(), "username": me["username"]},
    )


@app.get("/healthz")
async def healthz():
    host, port, key = await db.get_overseer_config()
    return {
        "ok": True,
        "version": __import__("m_status").__version__,
        "overseer_configured": bool(host and port and key),
        "overseer_connected":  overseer.client.connected,
        "monitor_last_run":    monitor.monitor.last_run_at,
    }
