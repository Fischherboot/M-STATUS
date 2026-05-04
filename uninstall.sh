#!/usr/bin/env bash
# Removes the M-STATUS systemd service. Optionally also wipes code and data.
set -euo pipefail

INSTALL_DIR="${M_STATUS_INSTALL_DIR:-/opt/m-status}"
DATA_DIR="${M_STATUS_DATA_DIR:-/var/lib/m-status}"
SERVICE_USER="${M_STATUS_USER:-mstatus}"
SERVICE_NAME="${M_STATUS_SERVICE_NAME:-m-status}"

[[ $EUID -eq 0 ]] || { echo "Bitte als root / mit sudo ausführen." >&2; exit 1; }

echo "Stoppe und entferne Service '$SERVICE_NAME'…"
systemctl stop "$SERVICE_NAME" 2>/dev/null || true
systemctl disable "$SERVICE_NAME" 2>/dev/null || true
rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload

read -r -p "Auch Code unter $INSTALL_DIR löschen? [y/N] " yn
if [[ "${yn,,}" == "y" ]]; then
    rm -rf "$INSTALL_DIR"
    echo "  → $INSTALL_DIR entfernt"
fi

read -r -p "Auch Daten unter $DATA_DIR löschen? (DATENBANK GEHT VERLOREN) [y/N] " yn
if [[ "${yn,,}" == "y" ]]; then
    rm -rf "$DATA_DIR"
    echo "  → $DATA_DIR entfernt"
fi

read -r -p "Service-User '$SERVICE_USER' löschen? [y/N] " yn
if [[ "${yn,,}" == "y" ]]; then
    userdel "$SERVICE_USER" 2>/dev/null || true
    echo "  → User '$SERVICE_USER' entfernt"
fi

echo "Fertig."
