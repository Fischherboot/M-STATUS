"""Configuration for M-STATUS.

Everything is driven by env vars with sensible defaults. No secrets in code.
Runtime config (overseer host/port/key, admin password) lives in the database.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# Where the SQLite db lives. Override with M_STATUS_DB_PATH.
DB_PATH: Path = Path(os.environ.get("M_STATUS_DB_PATH", "data/m-status.db")).resolve()

# Bind address / port for uvicorn. Default 3502 per spec.
HOST: str = os.environ.get("M_STATUS_HOST", "0.0.0.0")
PORT: int = int(os.environ.get("M_STATUS_PORT", "3502"))

# Session signing key. Auto-generated and persisted on first boot if not given.
# Override with M_STATUS_SECRET_KEY for multi-instance setups.
SECRET_KEY: str | None = os.environ.get("M_STATUS_SECRET_KEY")

# How many days of history to keep. The UI shows 90 by default.
HISTORY_DAYS: int = int(os.environ.get("M_STATUS_HISTORY_DAYS", "90"))


# ─── status thresholds (devices and services share the same scheme) ──────
#
# Every CHECK_INTERVAL seconds we run one probe. State by consecutive fails:
#   0 fails              → green   (healthy)
#   1..DEGRADED-1        → orange  (a hiccup; might be a reboot)
#   DEGRADED..OFFLINE-1  → red ("Fehlerhaft" — sustained problem)
#   ≥ OFFLINE            → red ("Offline"  — gave up; really gone)
#
# The two red sub-states share the same daily-history bucket (red), but the
# UI labels them differently so users can tell "still trying" from "dead".

CHECK_INTERVAL_SECONDS: int = int(
    os.environ.get("M_STATUS_CHECK_INTERVAL", "300")  # 5 min
)
DEGRADED_AFTER_FAILS: int = int(
    os.environ.get("M_STATUS_DEGRADED_AFTER", "4")    # 20 min sustained → red ("Fehlerhaft")
)
OFFLINE_AFTER_FAILS: int = int(
    os.environ.get("M_STATUS_OFFLINE_AFTER", "8")     # 40 min sustained → red ("Offline")
)

# How long a single ping/probe gets before we count it as a failure.
PROBE_TIMEOUT_SECONDS: float = float(
    os.environ.get("M_STATUS_PROBE_TIMEOUT", "5")
)

# Pings per service run (TCP / HTTP probes are cheap; one is enough but four
# smooths over flaky networks). Devices use a single ICMP per run.
SERVICE_PINGS_PER_RUN: int = int(os.environ.get("M_STATUS_SERVICE_PINGS", "4"))

# Daily REST sync with the overseer to learn about new/removed devices.
DEVICE_SYNC_INTERVAL_SECONDS: int = int(
    os.environ.get("M_STATUS_DEVICE_SYNC_INTERVAL", "86400")  # 24h
)

# Overseer WebSocket reconnect backoff. The WS is best-effort — REST sync is
# the source of truth for which devices exist.
OVERSEER_RECONNECT_MIN: float = 1.0
OVERSEER_RECONNECT_MAX: float = 30.0


# ─── legacy aliases (kept so existing env-vars in old systemd units work) ─
# Older M_STATUS_SERVICE_INTERVAL / M_STATUS_SERVICE_RED_AFTER / M_STATUS_REBOOT_THRESHOLD
# vars map onto the new settings if the new ones are not set explicitly.

if "M_STATUS_CHECK_INTERVAL" not in os.environ and "M_STATUS_SERVICE_INTERVAL" in os.environ:
    CHECK_INTERVAL_SECONDS = int(os.environ["M_STATUS_SERVICE_INTERVAL"])
if "M_STATUS_DEGRADED_AFTER" not in os.environ and "M_STATUS_SERVICE_RED_AFTER" in os.environ:
    DEGRADED_AFTER_FAILS = int(os.environ["M_STATUS_SERVICE_RED_AFTER"])
if "M_STATUS_PROBE_TIMEOUT" not in os.environ and "M_STATUS_PING_TIMEOUT" in os.environ:
    PROBE_TIMEOUT_SECONDS = float(os.environ["M_STATUS_PING_TIMEOUT"])


def get_or_create_secret_key(stored: str | None) -> str:
    """Return the secret key, generating one if needed.

    Priority: env var > stored in db > newly-generated (caller persists it).
    """
    if SECRET_KEY:
        return SECRET_KEY
    if stored:
        return stored
    return secrets.token_urlsafe(48)
