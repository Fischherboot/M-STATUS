#!/usr/bin/env bash
# ============================================================
#  M-STATUS · robust interactive installer
#  Author: Moritz and Zen
#
#  Sets up M-STATUS as a systemd service.
#  Usage: sudo ./install.sh
#
#  Optional non-interactive env vars:
#    M_STATUS_ASSUME_YES=1
#    M_STATUS_INSTALL_DIR=/opt/m-status
#    M_STATUS_DATA_DIR=/var/lib/m-status
#    M_STATUS_SERVICE_USER=mstatus
#    M_STATUS_SERVICE_NAME=m-status
#    M_STATUS_HOST=0.0.0.0
#    M_STATUS_PORT=3502
#    M_STATUS_ADMIN_USER=moritz
#    M_STATUS_ADMIN_PASS='change-me-please'
#    M_STATUS_OVERSEER_ENABLE=0|1
#    M_STATUS_OVERSEER_HOST=192.168.1.170
#    M_STATUS_OVERSEER_PORT=3501
#    M_STATUS_OVERSEER_API_KEY=observe-...
#    M_STATUS_SKIP_OVERSEER_TEST=0|1
# ============================================================
set -Eeuo pipefail

SCRIPT_NAME="$(basename "$0")"
LOG_FILE="/tmp/m-status-install.$$.log"

exec > >(tee -a "$LOG_FILE") 2>&1

on_error() {
    local exit_code=$?
    local line_no=${BASH_LINENO[0]:-unknown}
    echo
    echo "✗ Installer abgebrochen. Exit-Code: ${exit_code}, ungefähr bei Zeile: ${line_no}" >&2
    echo "  Log: ${LOG_FILE}" >&2
    echo "  Letzte Log-Zeilen:" >&2
    tail -n 25 "$LOG_FILE" >&2 || true
    exit "$exit_code"
}
trap on_error ERR

# ── colors ────────────────────────────────────────────────────
if [[ -t 1 ]]; then
    C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
    C_PURPLE=$'\033[38;5;141m'; C_ORANGE=$'\033[38;5;215m'
    C_GREEN=$'\033[38;5;114m';  C_RED=$'\033[38;5;167m'; C_BLUE=$'\033[38;5;111m'
else
    C_RESET=""; C_BOLD=""; C_DIM=""; C_PURPLE=""; C_ORANGE=""; C_GREEN=""; C_RED=""; C_BLUE=""
fi

step()  { echo "${C_PURPLE}▸${C_RESET} ${C_BOLD}$*${C_RESET}"; }
info()  { echo "  ${C_DIM}$*${C_RESET}"; }
ok()    { echo "  ${C_GREEN}✓${C_RESET} $*"; }
warn()  { echo "  ${C_ORANGE}!${C_RESET} $*"; }
fail()  { echo "${C_RED}✗${C_RESET} ${C_BOLD}$*${C_RESET}" >&2; exit 1; }

banner() {
    echo
    echo "${C_PURPLE}╔══════════════════════════════════════════╗${C_RESET}"
    echo "${C_PURPLE}║${C_RESET}  ${C_BOLD}M-${C_RESET}${C_ORANGE}STATUS${C_RESET}  ${C_DIM}· robust installer${C_RESET}       ${C_PURPLE}║${C_RESET}"
    echo "${C_PURPLE}╚══════════════════════════════════════════╝${C_RESET}"
    echo
}

# ── terminal/input handling ───────────────────────────────────
INTERACTIVE=0
TTY_FD_OPEN=0
ASSUME_YES="${M_STATUS_ASSUME_YES:-0}"

if [[ -r /dev/tty && -w /dev/tty ]]; then
    exec 3<>/dev/tty
    TTY_FD_OPEN=1
    INTERACTIVE=1
elif [[ -t 0 ]]; then
    INTERACTIVE=1
fi

read_line() {
    local __var_name="$1"
    local __input=""
    if [[ "$TTY_FD_OPEN" == "1" ]]; then
        IFS= read -r -u 3 __input
    else
        IFS= read -r __input
    fi
    printf -v "$__var_name" '%s' "$__input"
}

read_secret() {
    local __var_name="$1"
    local __input=""

    if [[ "$TTY_FD_OPEN" == "1" ]]; then
        local old_stty=""
        old_stty="$(stty -g < /dev/tty 2>/dev/null || true)"
        stty -echo < /dev/tty 2>/dev/null || true
        IFS= read -r -u 3 __input
        [[ -n "$old_stty" ]] && stty "$old_stty" < /dev/tty 2>/dev/null || true
        echo
    else
        warn "Keine echte TTY gefunden — Passwort-Eingabe wird eventuell sichtbar."
        IFS= read -r __input
    fi

    printf -v "$__var_name" '%s' "$__input"
}

ASK_RESULT=""
ask() {
    local prompt="$1" default="${2:-}" env_value="${3:-}"

    if [[ -n "$env_value" ]]; then
        ASK_RESULT="$env_value"
        info "$prompt: aus ENV übernommen"
        return 0
    fi

    if [[ "$ASSUME_YES" == "1" && -n "$default" ]]; then
        ASK_RESULT="$default"
        info "$prompt: $default"
        return 0
    fi

    if [[ "$INTERACTIVE" != "1" ]]; then
        if [[ -n "$default" ]]; then
            ASK_RESULT="$default"
            info "$prompt: $default"
            return 0
        fi
        fail "Nicht-interaktiv und kein Wert für '$prompt' gesetzt. Setz passende M_STATUS_* ENV-Variable."
    fi

    if [[ -n "$default" ]]; then
        printf "  ${C_BLUE}?${C_RESET} ${C_BOLD}%s${C_RESET} ${C_DIM}[%s]${C_RESET} " "$prompt" "$default"
    else
        printf "  ${C_BLUE}?${C_RESET} ${C_BOLD}%s${C_RESET} " "$prompt"
    fi

    local input=""
    read_line input
    [[ -z "$input" ]] && input="$default"
    ASK_RESULT="$input"
}

ask_required() {
    local prompt="$1" env_value="${2:-}"

    if [[ -n "$env_value" ]]; then
        ASK_RESULT="$env_value"
        info "$prompt: aus ENV übernommen"
        return 0
    fi

    while :; do
        ask "$prompt" ""
        [[ -n "$ASK_RESULT" ]] && return 0
        warn "Pflichtfeld."
    done
}

ask_secret_required() {
    local prompt="$1" env_value="${2:-}"

    if [[ -n "$env_value" ]]; then
        ASK_RESULT="$env_value"
        info "$prompt: aus ENV übernommen"
        return 0
    fi

    if [[ "$INTERACTIVE" != "1" ]]; then
        fail "Nicht-interaktiv und kein Passwort gesetzt. Setz M_STATUS_ADMIN_PASS."
    fi

    while :; do
        printf "  ${C_BLUE}?${C_RESET} ${C_BOLD}%s${C_RESET} " "$prompt"
        local input=""
        read_secret input
        [[ -n "$input" ]] && { ASK_RESULT="$input"; return 0; }
        warn "Pflichtfeld."
    done
}

yesno() {
    local prompt="$1" default="${2:-n}" env_value="${3:-}"
    local hint="[y/N]"
    [[ "$default" == "y" ]] && hint="[Y/n]"

    if [[ -n "$env_value" ]]; then
        case "${env_value,,}" in
            1|true|y|yes|j|ja) return 0 ;;
            0|false|n|no|nein) return 1 ;;
            *) fail "Ungültiger yes/no ENV-Wert für '$prompt': $env_value" ;;
        esac
    fi

    if [[ "$ASSUME_YES" == "1" ]]; then
        [[ "$default" == "y" ]] && return 0 || return 1
    fi

    if [[ "$INTERACTIVE" != "1" ]]; then
        [[ "$default" == "y" ]] && return 0 || return 1
    fi

    while :; do
        printf "  ${C_BLUE}?${C_RESET} ${C_BOLD}%s${C_RESET} ${C_DIM}%s${C_RESET} " "$prompt" "$hint"
        local ans=""
        read_line ans
        [[ -z "$ans" ]] && ans="$default"
        case "${ans,,}" in
            y|yes|j|ja) return 0 ;;
            n|no|nein)  return 1 ;;
            *) warn "Bitte y/n eingeben." ;;
        esac
    done
}

require_cmd() {
    local cmd="$1" pkg_hint="${2:-$1}"
    command -v "$cmd" >/dev/null 2>&1 || fail "'$cmd' fehlt. Installier es z. B. mit: apt install $pkg_hint"
}

validate_abs_path() {
    local name="$1" path="$2"
    [[ "$path" == /* ]] || fail "$name muss ein absoluter Pfad sein: $path"
}

validate_number() {
    local name="$1" value="$2"
    [[ "$value" =~ ^[0-9]+$ ]] || fail "$name muss eine Zahl sein: $value"
}

validate_service_name() {
    local value="$1"
    [[ "$value" =~ ^[A-Za-z0-9_.@-]+$ ]] || fail "Ungültiger systemd-Service-Name: $value"
}

validate_user_name() {
    local value="$1"
    [[ "$value" =~ ^[a-z_][a-z0-9_-]*[$]?$ ]] || fail "Ungültiger Linux-Username: $value"
}

validate_host() {
    local value="$1"
    # Hostnames or IPs only — no scheme, no slashes, no colons.
    [[ "$value" =~ ^[A-Za-z0-9._-]+$ ]] || fail "Ungültiger Host (kein Schema, kein Port, keine Slashes): $value"
}

run_as_service_user() {
    local user="$1"
    shift
    if command -v runuser >/dev/null 2>&1; then
        runuser -u "$user" -- "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo -u "$user" "$@"
    else
        fail "Weder runuser noch sudo gefunden — kann DB-Seed nicht als Service-User ausführen."
    fi
}

# ── pre-flight ────────────────────────────────────────────────
banner
[[ $EUID -eq 0 ]] || fail "Bitte mit sudo / als root ausführen."

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -d "$SOURCE_DIR/m_status" ]] || fail "m_status/ nicht gefunden — $SCRIPT_NAME aus dem entpackten M-STATUS-Verzeichnis starten."
[[ -f "$SOURCE_DIR/requirements.txt" ]] || fail "requirements.txt fehlt."

step "Systemcheck"
require_cmd systemctl systemd
require_cmd rsync rsync
require_cmd ping iputils-ping
ok "systemd, rsync und ping vorhanden"

PYTHON=""
for cand in python3.13 python3.12 python3.11 python3; do
    if command -v "$cand" >/dev/null 2>&1; then
        ver="$($cand -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "")"
        major="${ver%%.*}"
        minor="${ver##*.}"
        if [[ "$major" == "3" && "$minor" -ge 11 ]]; then
            PYTHON="$cand"
            break
        fi
    fi
done
[[ -n "$PYTHON" ]] || fail "Python 3.11+ wurde nicht gefunden. Installier es z. B. mit: apt install python3 python3-venv"
ok "Python: $($PYTHON --version 2>&1) ($(command -v "$PYTHON"))"

if ! "$PYTHON" -c "import venv" >/dev/null 2>&1; then
    fail "$PYTHON hat kein venv-Modul. Auf Debian/Ubuntu: apt install python3-venv"
fi
ok "venv-Modul vorhanden"

# ── interactive prompts ───────────────────────────────────────
echo
step "System-Pfade & Service"
info "Standardwerte mit Enter übernehmen."
echo

ask "Install-Verzeichnis" "/opt/m-status" "${M_STATUS_INSTALL_DIR:-}"; INSTALL_DIR="$ASK_RESULT"
ask "Daten-Verzeichnis (DB)" "/var/lib/m-status" "${M_STATUS_DATA_DIR:-}"; DATA_DIR="$ASK_RESULT"
ask "Service-User" "mstatus" "${M_STATUS_SERVICE_USER:-}"; SERVICE_USER="$ASK_RESULT"
ask "systemd-Service-Name" "m-status" "${M_STATUS_SERVICE_NAME:-}"; SERVICE_NAME="$ASK_RESULT"

validate_abs_path "Install-Verzeichnis" "$INSTALL_DIR"
validate_abs_path "Daten-Verzeichnis" "$DATA_DIR"
validate_user_name "$SERVICE_USER"
validate_service_name "$SERVICE_NAME"

echo
step "Netzwerk"
ask "Bind-Adresse (Host)" "0.0.0.0" "${M_STATUS_HOST:-}"; BIND_HOST="$ASK_RESULT"
ask "Port" "3502" "${M_STATUS_PORT:-}"; BIND_PORT="$ASK_RESULT"
validate_number "Port" "$BIND_PORT"
(( BIND_PORT >= 1 && BIND_PORT <= 65535 )) || fail "Port muss zwischen 1 und 65535 liegen."

echo
step "Tracking-Tuning"
info "Drück Enter wenn du dir nicht sicher bist — die Defaults sind sinnvoll."
echo
ask "History — wieviele Tage anzeigen?" "90" "${M_STATUS_HISTORY_DAYS:-}"; HISTORY_DAYS="$ASK_RESULT"
ask "Check-Intervall (Sek.) — alle X Sek. wird gepingt" "300" "${M_STATUS_CHECK_INTERVAL:-}"; CHECK_INTERVAL="$ASK_RESULT"
ask "Pings pro Service-Run" "4" "${M_STATUS_SERVICE_PINGS:-}"; SERVICE_PINGS="$ASK_RESULT"
ask "Probe-Timeout (Sek.) pro Ping" "5" "${M_STATUS_PROBE_TIMEOUT:-}"; PROBE_TIMEOUT="$ASK_RESULT"
ask "Failed Runs bis 'Fehlerhaft' (rot)" "4" "${M_STATUS_DEGRADED_AFTER:-}"; DEGRADED_AFTER="$ASK_RESULT"
ask "Failed Runs bis 'Offline'" "8" "${M_STATUS_OFFLINE_AFTER:-}"; OFFLINE_AFTER="$ASK_RESULT"

validate_number "History" "$HISTORY_DAYS"
validate_number "Check-Intervall" "$CHECK_INTERVAL"
validate_number "Pings pro Service-Run" "$SERVICE_PINGS"
validate_number "Probe-Timeout" "$PROBE_TIMEOUT"
validate_number "Degraded after" "$DEGRADED_AFTER"
validate_number "Offline after" "$OFFLINE_AFTER"

echo
step "Admin-Account"
info "Damit loggst du dich später in der Manage-UI ein."
echo

ask_required "Admin-Username" "${M_STATUS_ADMIN_USER:-}"; ADMIN_USER="$ASK_RESULT"

while :; do
    ask_secret_required "Admin-Passwort (≥ 8 Zeichen)" "${M_STATUS_ADMIN_PASS:-}"
    ADMIN_PASS="$ASK_RESULT"
    [[ ${#ADMIN_PASS} -lt 8 ]] && { warn "Mindestens 8 Zeichen."; M_STATUS_ADMIN_PASS=""; continue; }

    if [[ -n "${M_STATUS_ADMIN_PASS:-}" || "$ASSUME_YES" == "1" ]]; then
        break
    fi

    ask_secret_required "Passwort wiederholen"
    ADMIN_PASS2="$ASK_RESULT"
    [[ "$ADMIN_PASS" != "$ADMIN_PASS2" ]] && { warn "Passwörter stimmen nicht überein."; continue; }
    break
done
ok "Admin-Daten erfasst"

echo
step "M-OBSERVE Overseer (optional)"
info "Ohne Overseer: kein Geräte-Tracking, Geräte-Tab in der UI verschwindet."
echo

OVERSEER_HOST=""
OVERSEER_PORT=""
OVERSEER_KEY=""
SKIP_OVERSEER_TEST="false"

if yesno "M-OBSERVE Overseer einrichten?" "n" "${M_STATUS_OVERSEER_ENABLE:-}"; then
    ask_required "Overseer-Host (IP oder Hostname, kein http://)" "${M_STATUS_OVERSEER_HOST:-}"
    OVERSEER_HOST="$ASK_RESULT"
    validate_host "$OVERSEER_HOST"
    ask "Overseer-Port" "3501" "${M_STATUS_OVERSEER_PORT:-}"
    OVERSEER_PORT="$ASK_RESULT"
    validate_number "Overseer-Port" "$OVERSEER_PORT"
    (( OVERSEER_PORT >= 1 && OVERSEER_PORT <= 65535 )) || fail "Port muss zwischen 1 und 65535 liegen."
    ask_required "Overseer Plugin-API-Key (observe-…-….)" "${M_STATUS_OVERSEER_API_KEY:-}"
    OVERSEER_KEY="$ASK_RESULT"
    if yesno "Overseer-Verbindung jetzt testen?" "y" ""; then
        if [[ "${M_STATUS_SKIP_OVERSEER_TEST:-}" =~ ^(1|true|y|yes|j|ja)$ ]]; then
            SKIP_OVERSEER_TEST="true"
        else
            SKIP_OVERSEER_TEST="false"
        fi
    else
        SKIP_OVERSEER_TEST="true"
    fi
else
    info "OK — Standalone-Modus (nur Services)."
fi

echo
step "Branding (optional)"
info "Title und Footer-Link sind fest. Editierbar sind nur Untertitel, Manage-Label, Footer-Owner."
info "Auch das alles kann später in den Manage-Settings geändert werden."
echo

ask "Untertitel" "" "${M_STATUS_PAGE_SUBTITLE:-}"; PAGE_SUBTITLE="$ASK_RESULT"
ask "Footer-Owner" "Moritzsoft ©" "${M_STATUS_FOOTER_OWNER:-}"; FOOTER_OWNER="$ASK_RESULT"
ask "Manage-Link-Label" "Manage" "${M_STATUS_MANAGE_LABEL:-}"; MANAGE_LABEL="$ASK_RESULT"

# ── confirmation ──────────────────────────────────────────────
echo
step "Zusammenfassung"
echo "  Install:        $INSTALL_DIR"
echo "  Daten:          $DATA_DIR"
echo "  Service:        $SERVICE_NAME (User: $SERVICE_USER)"
echo "  Listen:         $BIND_HOST:$BIND_PORT"
echo "  Admin:          $ADMIN_USER"
if [[ -n "$OVERSEER_HOST" ]]; then
    echo "  Overseer:       $OVERSEER_HOST:$OVERSEER_PORT"
    echo "                  Test überspringen: $SKIP_OVERSEER_TEST"
else
    echo "  Overseer:       — (Standalone-Modus)"
fi
echo "  Untertitel:     ${PAGE_SUBTITLE:-(default)}"
echo "  Log:            $LOG_FILE"
echo

yesno "Mit diesen Werten installieren?" "y" || fail "Abgebrochen."

# ══════════════════════════════════════════════════════════════
#  Installation ab hier
# ══════════════════════════════════════════════════════════════
echo
step "Service-User '$SERVICE_USER'"
if id "$SERVICE_USER" >/dev/null 2>&1; then
    ok "User existiert bereits"
else
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
    ok "User angelegt"
fi

step "Code → $INSTALL_DIR"
if [[ "$SOURCE_DIR" == "$INSTALL_DIR" ]]; then
    ok "Source läuft bereits aus $INSTALL_DIR"
else
    if systemctl list-unit-files "${SERVICE_NAME}.service" >/dev/null 2>&1 && systemctl is-active --quiet "$SERVICE_NAME"; then
        info "Stoppe laufenden Service zum Update…"
        systemctl stop "$SERVICE_NAME"
    fi

    mkdir -p "$INSTALL_DIR"
    rsync -a --delete \
        --exclude="install.sh" \
        --exclude="moritz-install.sh" \
        --exclude="uninstall.sh" \
        --exclude="*.zip" \
        --exclude="*.rar" \
        --exclude=".venv" \
        --exclude="data" \
        --exclude=".git" \
        --exclude="__pycache__" \
        --exclude="*.pyc" \
        "$SOURCE_DIR"/ "$INSTALL_DIR"/
    ok "Code kopiert"
fi

[[ -d "$INSTALL_DIR/m_status" ]] || fail "Nach dem Kopieren fehlt $INSTALL_DIR/m_status — Paketstruktur stimmt nicht."
[[ -f "$INSTALL_DIR/requirements.txt" ]] || fail "Nach dem Kopieren fehlt $INSTALL_DIR/requirements.txt."

step "Daten-Verzeichnis $DATA_DIR"
mkdir -p "$DATA_DIR"
chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR"
chmod 750 "$DATA_DIR"
ok "Bereit"

step "Python venv + Dependencies"
VENV_DIR="$INSTALL_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
    "$PYTHON" -m venv "$VENV_DIR"
    ok "venv erstellt"
else
    ok "venv existiert"
fi

info "pip install läuft…"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -r "$INSTALL_DIR/requirements.txt"
ok "Dependencies installiert"

chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
chmod 755 "$INSTALL_DIR"

# ── DB seed ──────────────────────────────────────────────────
step "Datenbank seeden"
DB_PATH="$DATA_DIR/m-status.db"

CLI_ARGS=(
    --admin-user "$ADMIN_USER"
    --admin-pass "$ADMIN_PASS"
)
[[ -n "$OVERSEER_HOST"  ]] && CLI_ARGS+=(--overseer-host    "$OVERSEER_HOST")
[[ -n "$OVERSEER_PORT"  ]] && CLI_ARGS+=(--overseer-port    "$OVERSEER_PORT")
[[ -n "$OVERSEER_KEY"   ]] && CLI_ARGS+=(--overseer-api-key "$OVERSEER_KEY")
[[ -n "$PAGE_SUBTITLE"  ]] && CLI_ARGS+=(--page-subtitle    "$PAGE_SUBTITLE")
[[ -n "$FOOTER_OWNER"   ]] && CLI_ARGS+=(--footer-owner     "$FOOTER_OWNER")
[[ -n "$MANAGE_LABEL"   ]] && CLI_ARGS+=(--manage-label     "$MANAGE_LABEL")
[[ "$SKIP_OVERSEER_TEST" == "true" ]] && CLI_ARGS+=(--skip-overseer-test)

SKIP_SEED="false"
if [[ -f "$DB_PATH" ]]; then
    echo
    warn "Datenbank existiert bereits unter $DB_PATH"
    if yesno "Konfiguration zurücksetzen? (Admin + Overseer + Branding werden überschrieben, History bleibt erhalten)" "n" "${M_STATUS_FORCE_SEED:-}"; then
        CLI_ARGS+=(--force)
    else
        info "Behalte bestehende Konfiguration. Skipping seed."
        SKIP_SEED="true"
    fi
fi

if [[ "$SKIP_SEED" != "true" ]]; then
    if ! run_as_service_user "$SERVICE_USER" \
            env "M_STATUS_DB_PATH=$DB_PATH" \
                PYTHONPATH="$INSTALL_DIR" \
            "$VENV_DIR/bin/python" -m m_status.cli_setup "${CLI_ARGS[@]}"; then
        fail "DB-Seed fehlgeschlagen. Prüfe Overseer-Host/Port/API-Key oder starte mit M_STATUS_SKIP_OVERSEER_TEST=1."
    fi
    ok "DB-Seed fertig"
else
    ok "Bestehende DB unverändert"
fi

# Clear password variables from this shell as far as bash allows.
ADMIN_PASS=""
ADMIN_PASS2=""

# ── systemd unit ─────────────────────────────────────────────
step "systemd-Service"
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
cat > "$UNIT_FILE" <<EOF_UNIT
[Unit]
Description=M-STATUS · self-hosted status page
Documentation=https://343.im/MSTATUS
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
Environment="M_STATUS_DB_PATH=$DB_PATH"
Environment="M_STATUS_HOST=$BIND_HOST"
Environment="M_STATUS_PORT=$BIND_PORT"
Environment="M_STATUS_HISTORY_DAYS=$HISTORY_DAYS"
Environment="M_STATUS_CHECK_INTERVAL=$CHECK_INTERVAL"
Environment="M_STATUS_SERVICE_PINGS=$SERVICE_PINGS"
Environment="M_STATUS_PROBE_TIMEOUT=$PROBE_TIMEOUT"
Environment="M_STATUS_DEGRADED_AFTER=$DEGRADED_AFTER"
Environment="M_STATUS_OFFLINE_AFTER=$OFFLINE_AFTER"
Environment="PYTHONDONTWRITEBYTECODE=1"
ExecStart=$VENV_DIR/bin/python -m m_status
Restart=on-failure
RestartSec=5

# M-STATUS pings devices via ICMP. CAP_NET_RAW lets the unprivileged
# service-user invoke /usr/bin/ping under hardening.
AmbientCapabilities=CAP_NET_RAW
CapabilityBoundingSet=CAP_NET_RAW
NoNewPrivileges=true

# Hardening
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
ReadWritePaths=$DATA_DIR
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RestrictNamespaces=true
RestrictRealtime=true
RestrictSUIDSGID=true
LockPersonality=true
SystemCallArchitectures=native

[Install]
WantedBy=multi-user.target
EOF_UNIT
chmod 644 "$UNIT_FILE"
ok "Unit-File: $UNIT_FILE"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null
ok "Service auf Autostart gesetzt"

step "Service starten"
systemctl restart "$SERVICE_NAME"
sleep 2
if systemctl is-active --quiet "$SERVICE_NAME"; then
    ok "Service läuft"
else
    warn "Service läuft nicht — Logs:"
    journalctl -u "$SERVICE_NAME" --no-pager -n 80 || true
    fail "Service-Start fehlgeschlagen."
fi

# ── done ─────────────────────────────────────────────────────
HOST_ADDR="$(hostname -I 2>/dev/null | awk '{print $1}')"
[[ -z "$HOST_ADDR" ]] && HOST_ADDR="<host>"

echo
echo "${C_GREEN}════════════════════════════════════════════${C_RESET}"
echo "${C_GREEN}  Installation fertig${C_RESET}"
echo "${C_GREEN}════════════════════════════════════════════${C_RESET}"
echo
echo "  ${C_BOLD}Status-Page:${C_RESET}     http://${HOST_ADDR}:${BIND_PORT}/"
echo "  ${C_BOLD}Manage-UI:${C_RESET}       http://${HOST_ADDR}:${BIND_PORT}/manage"
echo "  ${C_BOLD}Login:${C_RESET}           http://${HOST_ADDR}:${BIND_PORT}/login"
echo
echo "  ${C_DIM}Logs:${C_RESET}     sudo journalctl -u $SERVICE_NAME -f"
echo "  ${C_DIM}Status:${C_RESET}   sudo systemctl status $SERVICE_NAME"
echo "  ${C_DIM}Restart:${C_RESET}  sudo systemctl restart $SERVICE_NAME"
echo "  ${C_DIM}Installer-Log:${C_RESET} $LOG_FILE"
echo
if [[ -n "$OVERSEER_HOST" ]]; then
    echo "  Du bist mit dem Overseer ($OVERSEER_HOST:$OVERSEER_PORT) verbunden."
    echo "  Geräte werden alle $CHECK_INTERVAL Sek. selbst gepingt."
    echo "  Inventar-Sync mit dem Overseer läuft täglich."
else
    echo "  Standalone-Modus aktiv. Geräte-Tab in der UI ist ausgeblendet."
    echo "  Du kannst jederzeit unter Settings einen Overseer hinzufügen."
fi
echo
