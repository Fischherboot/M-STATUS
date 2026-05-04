<p align="center">
  <img src="https://raw.githubusercontent.com/Fischherboot/M-STATUS/refs/heads/main/img/logo.png" alt="M-STATUS" width="700">
</p>

<p align="center">
  <strong>Self-hosted Status-Page für Homelabs.</strong><br>
  Glasmorphes UI · Aktives Probing · M-OBSERVE Integration (optional) · Kein Cloud-Zwang
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Lizenz-MSOL-blue" alt="Lizenz">
  <img src="https://img.shields.io/badge/Backend-FastAPI-009688" alt="FastAPI">
  <img src="https://img.shields.io/badge/Frontend-Vanilla_JS-f7df1e" alt="JS">
  <img src="https://img.shields.io/badge/DB-SQLite-003b57" alt="SQLite">
</p>

<p align="center">
  <strong>Live-Demo:</strong> <a href="https://status.moritzsoft.de/">status.moritzsoft.de</a>
</p>

---

## Was ist M-STATUS?

M-STATUS ist eine schlanke Status-Page für Homelabs und kleine Setups. Geräte und Services werden alle paar Minuten aktiv geprobt (ICMP für Geräte, HTTP/TCP für Services), das Ergebnis landet in einem 90-Tage-Verlauf mit hübschen Balken und einem klaren Statusbanner. Keine Cloud, keine Accounts, keine Telemetrie — nur Python, SQLite und ein Browser.

Optional kann sich M-STATUS an einen [**M-OBSERVE**](https://github.com/Fischherboot/M-OBSERVE)-Overseer hängen und sein Geräte-Inventar live von dort beziehen. Wer kein M-OBSERVE betreibt, lässt das Feld einfach leer und nutzt M-STATUS standalone für reine Service-Überwachung.

---

## Screenshots

### Status-Page — Public View

![BILD_LINK_HIER_—_Öffentliche_Status-Page_mit_Geräten_und_Services_als_Karten,_90-Tage_Uptime-Balken,_Status-Banner_oben](https://raw.githubusercontent.com/Fischherboot/M-STATUS/refs/heads/main/img/status.png)

> Die öffentliche Ansicht. Geräte und Services in Kategorien gruppiert, jede Zeile mit einem 90-Tage-Verlaufsstrip und Live-Status-Pill. Oben das Gesamt-Banner — grün, orange oder rot, je nachdem ob alles tickt.

### Admin-Panel — Verwaltung

![BILD_LINK_HIER_—_Admin_Settings_Panel_mit_Tabs_für_Geräte,_Services,_Kategorien,_Branding](https://raw.githubusercontent.com/Fischherboot/M-STATUS/refs/heads/main/img/admin.png)

> Geräte umbenennen, ausblenden, kategorisieren und per Drag & Drop sortieren. Hier wird auch die Overseer-Verbindung, das Branding (Subtitle, Footer-Owner) und der Admin-Zugang verwaltet.

### Service hinzufügen

![BILD_LINK_HIER_—_Modal_Dialog_zum_Anlegen_eines_neuen_Services_mit_Feldern_für_Name,_URL,_Kategorie,_Lokal/Extern](https://raw.githubusercontent.com/Fischherboot/M-STATUS/refs/heads/main/img/newservice.png)

> Service anlegen: Name, URL, Kategorie, lokal vs. extern. Lokale Services werden gegen `internal_host:port` geprobt — keine falschen Greens durch einen Cloudflare Tunnel der "up" ist während der Origin am Boden liegt.

---

## Features

- **Aktives Probing** — alle 5 Minuten ICMP-Ping pro Gerät, HTTP-Probe pro Service. Der Overseer ist nur Inventar-Quelle, der Status-Wahrheitswert kommt von M-STATUS selbst.
- **Vier-stufiger Statusautomat** — `Online → Probleme → Fehlerhaft → Offline`, basierend auf einem einzigen Fail-Counter. Identisches Schema für Geräte und Services.
- **90-Tage Verlaufsbalken** — pro UTC-Tag ein Slot, schlechtester Status des Tages gewinnt (rot > orange > grün).
- **Lokale vs. externe Services** — externe werden per HTTP gegen die öffentliche URL geprobt, lokale per TCP gegen `internal_host:port`. Kein False-Positive-Theater mehr durch Tunnel-Frontends.
- **Kategorien & Sortierung** — Drag & Drop für Geräte und Services im Admin-Panel.
- **Glasmorphes UI** — dunkles Theme, Lila-zu-Orange-Gradient, animierter Particle-Hintergrund.
- **Cookie-Sessions, bcrypt-Passwörter** — keine JWTs, keine Third-Party-Auth.
- **Single-Box-Setup** — pures Python + SQLite, kein Redis, kein Postgres.
- **M-OBSERVE Integration optional** — komplette Service-Überwachung läuft auch ohne Overseer.

---

## M-OBSERVE Integration (optional)

Wenn ein [M-OBSERVE](https://github.com/Fischherboot/M-OBSERVE)-Overseer mit aktiviertem Plugin-Protokoll erreichbar ist, holt sich M-STATUS sein Geräte-Inventar von dort:

| Kanal | Zweck |
|---|---|
| **REST** `GET /api/plugin/devices` | Voller Inventar-Snapshot beim Start und einmal täglich. Quelle der Wahrheit für *welche Geräte existieren*. |
| **WebSocket** `/ws/plugin` | Live-Events `device_online` / `device_offline` / `device_deleted`. Best-effort, fällt der Sync täglich nochmal zurecht. |

Wichtig: M-STATUS **vertraut** den `device_online`/`offline`-Events nicht blind. Ein klebriges WebSocket auf Overseer-Seite hat schon mal tote Kisten als "online" markiert. Deswegen wird jedes Gerät zusätzlich aktiv gepingt — der Overseer liefert nur Hostname, IP und Anzeigename, der Reachability-Check passiert lokal.

Auth läuft über den gleichen API-Key den auch die M-OBSERVE-Clients nutzen (`observe-<wort>-<4ziffern>`, zu finden im Overseer unter Settings → API-Key). Read-only Protokoll: M-STATUS kann nichts auf dem Overseer schreiben, neustarten oder shellen.

**Standalone-Modus:** Im Setup-Wizard einfach "Overseer einrichten?" auf "no" lassen. Geräte können dann nicht getrackt werden, aber Services laufen ganz normal weiter.

---

## Architektur

```
┌─────────────────────────────────────┐
│           Browser (SPA)             │
│         http://<IP>:3502            │
└──────────────┬──────────────────────┘
               │ HTTP
┌──────────────▼──────────────────────┐
│    M-STATUS (FastAPI + uvicorn)     │
│           Port 3502                 │
│  ┌──────────┐  ┌─────────────────┐  │
│  │ REST API │  │ Probe Loop      │  │
│  │ + Pages  │  │ (alle 5 min)    │  │
│  └──────────┘  └─────────────────┘  │
│  ┌──────────────────────────────┐   │
│  │ SQLite (Config + 90 Tage     │   │
│  │ History pro Target)          │   │
│  └──────────────────────────────┘   │
└──────┬─────────────────────┬────────┘
       │ ICMP / HTTP / TCP   │ WS + REST (optional)
       ▼                     ▼
┌─────────────┐       ┌─────────────┐
│  Geräte &   │       │  M-OBSERVE  │
│  Services   │       │   Overseer  │
└─────────────┘       └─────────────┘
```

---

## Status-Logik

Eine Regel für Geräte und Services: pro Probe-Run gibt's entweder ✓ oder ✗. Aus dem Fail-Counter folgt direkt der Live-Status:

| Fehlschläge in Folge | Live-Status | Tages-Slot |
|---|---|---|
| 0 | 🟢 Online | grün |
| 1..3 | 🟠 Probleme | orange |
| 4..7 | 🔴 Fehlerhaft | rot |
| ≥ 8 | 🔴 Offline | rot |

Bei 5-Minuten-Intervallen heißt das: ein verpasster Ping → orange. Nach ~20 Minuten ohne Antwort → rot mit Label *Fehlerhaft*. Nach ~40 Minuten → *Offline*. Beide Rot-Zustände teilen sich den Tages-Slot, nur das Live-Label unterscheidet sich.

Uptime-Prozent: `(grün × 1.0 + orange × 0.5) / Tage_mit_Daten`. Tage ohne Probe-Daten zählen nicht gegen das Gerät.

---

## Setup

### Schnell-Install (Linux, systemd)

```bash
unzip m-status.zip
cd m-status
sudo ./install.sh
```

Der Installer ist vollständig interaktiv und fragt alles ab: Pfade, Port, Probe-Tuning, Admin-Account, optionaler Overseer (Host + Port + API-Key), Branding. Danach legt er einen `mstatus`-User an, kopiert nach `/opt/m-status`, baut ein venv, schreibt einen gehärteten systemd-Unit und startet alles.

Deinstallieren mit `sudo ./uninstall.sh`.

### Manuell

```bash
git clone https://github.com/Fischherboot/M-STATUS.git
cd M-STATUS
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m m_status
```

Beim ersten Start auf `http://<host>:3502/` öffnen — leitet zum Web-Wizard auf `/setup`.

### Docker

```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends iputils-ping \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV M_STATUS_DB_PATH=/data/m-status.db
VOLUME /data
EXPOSE 3502
CMD ["python", "-m", "m_status"]
```

```bash
docker run -d --name m-status -p 3502:3502 -v m-status-data:/data \
    --cap-add=NET_RAW m-status
```

`--cap-add=NET_RAW` ist nötig damit ICMP im Container funktioniert.

---

## Konfiguration

Alle Env-Vars sind optional mit sinnvollen Defaults. Runtime-Config (Overseer-Daten, Branding, Admin-Account) liegt in der SQLite-DB und wird im Admin-Panel verwaltet.

| Variable | Default | Bedeutung |
|---|---|---|
| `M_STATUS_HOST` | `0.0.0.0` | Bind-Adresse |
| `M_STATUS_PORT` | `3502` | Port |
| `M_STATUS_DB_PATH` | `data/m-status.db` | SQLite-DB-Pfad |
| `M_STATUS_HISTORY_DAYS` | `90` | Tage History speichern & anzeigen |
| `M_STATUS_CHECK_INTERVAL` | `300` | Sekunden zwischen Probe-Runs |
| `M_STATUS_DEGRADED_AFTER` | `4` | Fails bis "Fehlerhaft" |
| `M_STATUS_OFFLINE_AFTER` | `8` | Fails bis "Offline" |
| `M_STATUS_PROBE_TIMEOUT` | `5` | Sekunden pro einzelnem Probe |
| `M_STATUS_DEVICE_SYNC_INTERVAL` | `86400` | Sekunden zwischen REST-Inventar-Syncs |
| `M_STATUS_SECRET_KEY` | (auto) | Cookie-Session-Key. Setzen wenn mehrere Instanzen hinter einem LB laufen. |

---

## Voraussetzungen

- Python 3.11+
- `/usr/bin/ping` (CAP_NET_RAW wird vom systemd-Unit gesetzt)
- *Optional:* erreichbarer M-OBSERVE-Overseer mit Plugin-Protokoll für Geräte-Tracking

---

## Ressourcenverbrauch

Bewusst minimal gehalten. Keine Zeitreihen-DB, kein Prometheus, kein Grafana. SQLite speichert Config, Targets und 90 Tage Tages-History. Footprint inklusive Python-venv unter **150 MB RAM**.

---

## Lizenz

<p align="center">
  <a href="https://moritzsoft.de/#license">Moritzsoft Open License v1.1</a>
</p>

<p align="center">
  M-STATUS | <a href="https://343.im/MSTATUS">343.im/MSTATUS</a> | Moritzsoft ©
</p>
