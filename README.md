# orc

Personal home automation orchestrator. Drives lights and Chromecast speakers
on a schedule built from sunrise/sunset, calendar events, and a markdown
config file.

## What it does

- Runs themed daily routines (e.g. *work day* / *day off*) with events tied to
  wall-clock times or sun position at a configured lat/long.
- Pulls calendar events from an iCal feed and schedules audio alerts /
  routines around them.
- Skips market-holiday rules via a configurable holidays endpoint.
- Controls Hubitat lights (REST) and Chromecast speakers (pychromecast +
  yt-dlp for YouTube audio).
- Serves a small Flask UI for manual control, schedule inspection, theme
  override, and an activity log.

## Requirements

- Python 3.10+
- A reachable Hubitat Maker API endpoint (`BASE_URL`)
- One or more Chromecast devices on the local network
- A Bitwarden Secrets Manager account holding the runtime secrets (see below)
- Optional: TLS cert/key if you want HTTPS on :443

## Configuration

Two config surfaces:

1. **Markdown config** at `src/config.md` (override path with `ORC_CONFIG`).
   Defines devices, routines, themes, room configs, ad-hoc routines, super
   routines, and button highlights. See the existing file for the table
   schemas.
2. **Environment variables** (read in `src/orc/config.py`):

   | Var                 | Purpose                                        | Default               |
   |---------------------|------------------------------------------------|-----------------------|
   | `ENABLED`           | Local-dev opt-in: talk to real devices/secrets | unset (offline)       |
   | `BASE_URL`          | Hubitat Maker API base URL                     | —                     |
   | `ORC_CONFIG`        | Path to markdown config                        | `src/config.md`       |
   | `TZ`                | IANA timezone                                  | `America/New_York`    |
   | `LAT`               | Latitude for sunrise/sunset                    | `40.7143`             |
   | `LONG`              | Longitude for sunrise/sunset                   | `-74.0060`            |
   | `SSL_KEY`           | Path to TLS key (enables HTTPS on :443)        | unset                 |
   | `SSL_CERT`          | Path to TLS cert                               | unset                 |
   | `HTTP_TIMEOUT`      | Default outbound HTTP timeout (s)              | `5`                   |
   | `HTTP_ICAL_TIMEOUT` | Timeout for the iCal fetch (s)                 | `120`                 |
   | `BWS_ACCESS_TOKEN`  | URL whose body is the Bitwarden access token   | required if `ENABLED` |
   | `BWS_ORG_ID`        | URL whose body is the Bitwarden org ID         | required if `ENABLED` |

   In production `ENABLED` is always set; leave it unset locally to run
   without hitting Hubitat, Chromecasts, or Bitwarden.

   `BWS_ACCESS_TOKEN` and `BWS_ORG_ID` are URLs (e.g. `data:` or `file://`),
   not the values themselves — `dal._get_url_value` reads the body.

## Secrets (Bitwarden)

When `ENABLED` is set, three secrets are pulled from Bitwarden Secrets Manager
by name:

| Key                  | Used for                                            |
|----------------------|-----------------------------------------------------|
| `ACCESS_TOKEN`       | Hubitat Maker API access token (appended as query)  |
| `MARKET_HOLIDAYS_URL`| JSON endpoint returning market holiday dates        |
| `ICS_URL`            | iCal feed URL for calendar-driven routines          |

## Running

Local dev (uses the markdown config; talks to real devices when `ENABLED` is
set):

```sh
env PYTHONPATH=src \
    ENABLED=1 \
    BASE_URL=http://hubitat.local/apps/api/123 \
    BWS_ACCESS_TOKEN=file:///path/to/bws-token \
    BWS_ORG_ID=file:///path/to/bws-org-id \
    python -c 'from orc.runner import web; web()'
```

Or use the included `dev.sh` (tcsh, sources a venv at `~/.venv-orc`).

Tests:

```sh
PYTHONPATH=src pytest
```

## Deploy

`sh make.sh` builds a wheel and uploads it to the internal package registry.

## Layout

- `src/orc/runner.py` — Flask + APScheduler entry points (`web`, `worker`, `test`)
- `src/orc/api.py` — schedule construction, rule routing, config manager
- `src/orc/model.py` — markdown → config parsing and routine/theme types
- `src/orc/dal.py` — Hubitat, Chromecast, iCal, Bitwarden integrations
- `src/orc/view.py` + `templates/` — Flask UI
- `src/config.md` — device/routine/theme definitions
