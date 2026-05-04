"""Public, read-only API.

Returns the data shown on the status page: visible devices and services,
both grouped by category, plus an overall status banner.
"""
from __future__ import annotations

import time

from fastapi import APIRouter

from . import config, db, overseer, status

router = APIRouter(prefix="/api/public", tags=["public"])


def _device_payload(row: dict, strip: list[dict]) -> dict:
    return {
        "client_id":   row["client_id"],
        "name":        row["name"],
        "live":        status.device_live_colour(row),
        "live_label":  status.device_live_label(row),
        "online":      bool(row["online"]),
        "last_check":  row.get("last_check"),
        "last_seen":   row.get("last_seen"),
        "fail_runs":   int(row.get("consecutive_fail_runs") or 0),
        "uptime_pct":  status.uptime_percent(strip),
        "history":     strip,
    }


def _service_payload(row: dict, strip: list[dict]) -> dict:
    return {
        "id":          row["id"],
        "name":        row["name"],
        "url":         row["external_url"],
        "is_local":    bool(row["is_local"]),
        "live":        status.service_live_colour(row),
        "live_label":  status.service_live_label(row),
        "last_check":  row.get("last_check"),
        "fail_runs":   int(row.get("consecutive_fail_runs") or 0),
        "uptime_pct":  status.uptime_percent(strip),
        "history":     strip,
    }


def _overall(items: list[dict]) -> str:
    """Worst live colour across all displayed items."""
    out = status.GREEN
    for it in items:
        if it["live"] in (status.RED, status.ORANGE):
            out = status.worst(out, it["live"])
    return out


def _group_by_category(rows: list[dict], categories: list[dict],
                       fallback_name: str) -> list[dict]:
    """Split rows into category groups, preserving category order."""
    by_cat: dict[int | None, list] = {None: []}
    for c in categories:
        by_cat[c["id"]] = []
    for r in rows:
        by_cat.setdefault(r.get("category_id"), []).append(r)

    out = []
    for c in categories:
        items = by_cat.get(c["id"], [])
        if items:
            out.append({"id": c["id"], "name": c["name"], "items": items})
    if by_cat[None]:
        out.append({"id": None, "name": fallback_name, "items": by_cat[None]})
    return out


@router.get("/status")
async def public_status() -> dict:
    branding = await db.get_branding()

    async with db.db().execute(
        "SELECT id, name, sort_order FROM categories ORDER BY sort_order, name"
    ) as cur:
        categories = [dict(r) for r in await cur.fetchall()]

    # ── devices ──
    async with db.db().execute(
        "SELECT * FROM devices WHERE hidden=0 AND deleted=0 "
        "ORDER BY sort_order, name"
    ) as cur:
        device_rows = [dict(r) for r in await cur.fetchall()]

    devices_with_strips: list[dict] = []
    for r in device_rows:
        strip = await status.get_device_strip(r["client_id"])
        devices_with_strips.append({
            **_device_payload(r, strip),
            "category_id": r["category_id"],
        })
    devices_block = _group_by_category(devices_with_strips, categories, "Allgemein")

    # ── services ──
    async with db.db().execute(
        "SELECT * FROM services ORDER BY sort_order, name"
    ) as cur:
        service_rows = [dict(r) for r in await cur.fetchall()]

    services_with_strips: list[dict] = []
    for r in service_rows:
        strip = await status.get_service_strip(r["id"])
        services_with_strips.append({
            **_service_payload(r, strip),
            "category_id": r["category_id"],
        })
    services_block = _group_by_category(services_with_strips, categories, "Allgemein")

    # ── overall banner ──
    all_live = devices_with_strips + services_with_strips
    overall = _overall(all_live) if all_live else status.GREEN

    # Overseer connectivity: configured if we have host+port+key.
    host, port, key = await db.get_overseer_config()
    overseer_configured = bool(host and port and key)

    return {
        "branding":     branding,
        "history_days": config.HISTORY_DAYS,
        "overall":      overall,
        "overseer": {
            "connected":  overseer.client.connected,
            "configured": overseer_configured,
        },
        "categories":   devices_block,    # name kept for backward compat
        "service_groups": services_block,
        "services":     services_with_strips,  # flat list, kept for compat
        "generated_at": time.time(),
    }
