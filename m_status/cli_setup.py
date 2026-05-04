"""
Command-line setup for M-STATUS.

Used by install.sh to seed the database in one shot — no web wizard.

Usage:
    python -m m_status.cli_setup \\
        --admin-user admin \\
        --admin-pass 'hunter2…' \\
        [--overseer-host 192.168.1.170] \\
        [--overseer-port 3501] \\
        [--overseer-api-key observe-foo-1234] \\
        [--page-subtitle 'Service-Status'] \\
        [--footer-owner 'Example ©'] \\
        [--manage-label 'Manage'] \\
        [--skip-overseer-test] \\
        [--force]

Exit codes:
    0  — success
    1  — validation / overseer-test failure
    2  — already configured (without --force)
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from . import auth, db, overseer
from .routes_setup import normalise_host, normalise_port


C_OK   = "\033[32m✓\033[0m"
C_ERR  = "\033[31m✗\033[0m"
C_WARN = "\033[33m!\033[0m"
C_DIM  = "\033[2m"
C_END  = "\033[0m"


def _print_ok(msg: str) -> None:    print(f"  {C_OK} {msg}")
def _print_warn(msg: str) -> None:  print(f"  {C_WARN} {msg}")
def _print_err(msg: str) -> None:   print(f"  {C_ERR} {msg}", file=sys.stderr)
def _print_dim(msg: str) -> None:   print(f"  {C_DIM}{msg}{C_END}")


async def _wipe_existing_config() -> None:
    """Reset for --force: drop admins, clear overseer creds, drop sessions.

    History (devices, services, history rows, categories) is preserved.
    """
    conn = db.db()
    await conn.execute("DELETE FROM admins")
    await conn.execute("DELETE FROM sessions")
    await conn.commit()
    for k in ("overseer_host", "overseer_port", "overseer_api_key", "setup_complete"):
        await db.kv_delete(k)


async def _async_main(args: argparse.Namespace) -> int:
    await db.init()
    try:
        return await _do_setup(args)
    finally:
        await db.close()


async def _do_setup(args: argparse.Namespace) -> int:
    already_done = await db.is_setup_complete()
    if already_done and not args.force:
        _print_err("Setup ist bereits abgeschlossen.")
        _print_dim("Mit --force erzwingen (löscht Admin + Overseer-Config, History bleibt).")
        return 2

    if already_done and args.force:
        _print_warn("Bestehende Konfiguration wird überschrieben (--force)")
        await _wipe_existing_config()

    raw_host = (args.overseer_host or "").strip()
    raw_port = args.overseer_port
    raw_key  = (args.overseer_api_key or "").strip()
    has_overseer = bool(raw_host and raw_port and raw_key)

    if has_overseer:
        try:
            host = normalise_host(raw_host)
            port = normalise_port(raw_port)
        except ValueError as e:
            _print_err(f"Ungültige Overseer-Adresse: {e}")
            return 1

        if args.skip_overseer_test:
            _print_warn("Overseer-Test übersprungen (--skip-overseer-test)")
        else:
            ok, msg, _ = await overseer.fetch_inventory(host, port, raw_key)
            if not ok:
                _print_err(f"Overseer-Test fehlgeschlagen: {msg}")
                _print_dim(f"Adresse: {host}:{port}")
                _print_dim("Mit --skip-overseer-test trotzdem speichern.")
                return 1
            _print_ok(f"Overseer erreichbar: {host}:{port}")

        await db.set_overseer_config(host, port, raw_key)
    else:
        await db.set_overseer_config("", 0, "")

    # Persist branding (only what's actually editable now)
    branding_keys = [
        ("page_subtitle", args.page_subtitle),
        ("footer_owner",  args.footer_owner),
        ("manage_label",  args.manage_label),
    ]
    for key, val in branding_keys:
        if val:
            await db.kv_set(key, val.strip())

    # Admin user
    try:
        admin_id = await auth.create_admin(args.admin_user, args.admin_pass)
    except ValueError as e:
        _print_err(f"Admin-User: {e}")
        return 1

    await db.kv_set("setup_complete", "1")

    _print_ok(f"Admin '{args.admin_user}' angelegt (id={admin_id})")
    _print_ok(f"Setup abgeschlossen — {'Overseer aktiv' if has_overseer else 'Standalone-Modus'}")

    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="m_status.cli_setup",
        description="Seed M-STATUS DB without going through the web wizard.",
    )
    p.add_argument("--admin-user", required=True, help="Admin username (web login)")
    p.add_argument("--admin-pass", required=True, help="Admin password (≥ 8 Zeichen)")

    p.add_argument("--overseer-host", default="",
                   help="M-OBSERVE host or IP (no scheme), e.g. 192.168.1.170. "
                        "Leer lassen für Standalone-Modus.")
    p.add_argument("--overseer-port", type=int, default=0,
                   help="M-OBSERVE port, e.g. 3501. 0 = nicht konfiguriert.")
    p.add_argument("--overseer-api-key", default="",
                   help="M-OBSERVE plugin API key. Leer lassen für Standalone-Modus.")

    p.add_argument("--page-subtitle", default="", help="Subtitle under the logo")
    p.add_argument("--footer-owner",  default="", help="Owner string in the footer (e.g. 'ACME ©')")
    p.add_argument("--manage-label",  default="", help="Label for the [Manage] link in the footer")

    p.add_argument("--skip-overseer-test", action="store_true",
                   help="Don't probe the overseer before saving (still saves host+port+key).")
    p.add_argument("--force", action="store_true",
                   help="Overwrite existing config. History is preserved.")
    return p


def main() -> None:
    args = _build_parser().parse_args()
    rc = asyncio.run(_async_main(args))
    sys.exit(rc)


if __name__ == "__main__":
    main()
