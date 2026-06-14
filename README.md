# orc

Personal home automation orchestrator. Drives lights, Chromecast speakers,
and an LG TV on a schedule built from sunrise/sunset, calendar events, and
a markdown config file.

## What it does

- Runs themed daily routines (e.g. *work day* / *day off*) with events tied to
  wall-clock times or sun position at a configured lat/long.
- Pulls calendar events from an iCal feed and schedules audio alerts /
  routines around them.
- Skips market-holiday rules via a configurable holidays endpoint.
- Controls Hubitat lights (REST), Chromecast speakers (pychromecast + yt-dlp
  for YouTube audio), and an LG webOS TV (aiowebostv + Wake-on-LAN).
- Serves a small Flask UI for manual control, schedule inspection, theme
  override, and an activity log.

## Requirements

- A reachable Hubitat Maker API endpoint (`ORC_BASE_URL`)
- One or more Chromecast devices on the local network
- A Bitwarden Secrets Manager account holding the runtime secrets (see below)

## Configuration

Two config surfaces:

1. **Markdown config** at `src/config.md` (override path with `ORC_CONFIG`).
   Defines devices, routines, themes, room configs, ad-hoc routines, super
   routines, and button highlights. See the existing file for the table
   schemas.
2. **Environment variables** (read in `src/orc/__init__.py`):

   | Var                   | Purpose                                                            | Default                          |
   |-----------------------|--------------------------------------------------------------------|----------------------------------|
   | `ORC_ENABLED`         | Opt-in: talk to real devices/secrets; unset = offline/dry-run      | unset                            |
   | `ORC_BASE_URL`        | Hubitat Maker API base URL                                         | unset                            |
   | `ORC_CONFIG`          | Path to markdown config                                            | `src/config.md`                  |
   | `ORC_DB`              | SQLAlchemy URL for the APScheduler / orc state DB                  | `sqlite:////tmp/jobs.sqlite`     |
   | `ORC_TZ`              | IANA timezone                                                      | `America/New_York`               |
   | `ORC_LAT`             | Latitude for sunrise/sunset                                        | `40.7143`                        |
   | `ORC_LONG`            | Longitude for sunrise/sunset                                       | `-74.0060`                       |
   | `ORC_HTTP_TIMEOUT`    | Default outbound HTTP timeout (s)                                  | `5`                              |
   | `ORC_HTTP_ICAL_TIMEOUT` | Timeout for the iCal fetch (s)                                   | `120`                            |
   | `ORC_ROOT_DOMAIN`     | Trailing domain stripped from hostnames in the presence view       | `""`                             |
   | `ORC_INTERNAL_URL`    | LAN-reachable base URL Chromecasts use to fetch static audio       | `""` (falls back to request host) |
   | `BWS_ACCESS_TOKEN`    | URL whose body is the Bitwarden access token                       | required if `ORC_ENABLED`        |
   | `BWS_ORG_ID`          | URL whose body is the Bitwarden org ID                             | required if `ORC_ENABLED`        |

   `BWS_ACCESS_TOKEN` and `BWS_ORG_ID` are URLs (e.g. `data:` or `file://`),
   not the values themselves — the body of the URL is read at startup.

### Minimum for local development

With `ORC_ENABLED` unset, Hubitat/Chromecast/Bitwarden are not contacted
and no env vars are required — defaults are sufficient.

## Secrets (Bitwarden)

When `ORC_ENABLED` is set, three secrets are pulled from Bitwarden Secrets
Manager by name:

| Key                    | Used for                                            |
|------------------------|-----------------------------------------------------|
| `HUBITAT_ACCESS_TOKEN` | Hubitat Maker API access token (appended as query)  |
| `MARKET_HOLIDAYS_URL`  | JSON endpoint returning market holiday dates        |
| `ICS_URL`              | iCal feed URL for calendar-driven routines          |

## Running

Two entry points in `src/orc/runner.py`:

- `web()` — gunicorn, bound to `0.0.0.0:8000` (used in production)
- `flask()` — Flask's dev server on `0.0.0.0:8000`

`pip install` exposes `web()` as the `orc` console script
(`[project.scripts]` in `pyproject.toml`).

Local dev (talks to real devices when `ORC_ENABLED` is set):

```sh
env ORC_ENABLED=1 \
    ORC_BASE_URL=http://hubitat.local/apps/api/123 \
    BWS_ACCESS_TOKEN=file:///path/to/bws-token \
    BWS_ORG_ID=file:///path/to/bws-org-id \
    python -c 'from orc.runner import flask; flask()'
```

Tests (pytest's `pythonpath` is set in `pyproject.toml`):

```sh
pytest
```

## Deploy

`sh make.sh` builds a wheel and uploads it to the internal package registry.
Pass `full` to also rebuild and upload the `orc_data` sub-package:

```sh
sh make.sh full
```

`install.sh` is intended to run on the target host (invoked over SSH by
`build-and-install.sh`); it uninstalls the running version, reinstalls from
the internal registry, and bounces the `orc` supervisor job.

## Layout

- `src/orc/__init__.py` — `Config` (env vars + markdown-config loader)
- `src/orc/runner.py` — Flask + APScheduler entry points (`web`, `flask`)
- `src/orc/api.py` — schedule construction, rule routing, `ConfigManager`
- `src/orc/model.py` — markdown → config parsing and routine/theme types
- `src/orc/dal/` — integrations split by target: `lights.py` (Hubitat),
  `chromecast.py`, `tv.py` / `lgtv.py` (LG webOS + WoL), `feeds.py` (iCal /
  market holidays), `bws.py` (Bitwarden), `discovery.py`, `sqlite.py`,
  `_decorators.py`
- `src/orc/plugins.py` — plugin functions (reboot, sensor handler, …)
- `src/orc/apscheduler.py` — context-injecting executor and `requires_ctx`
- `src/orc/locale.py` — log-message string constants
- `src/orc/view.py` + `templates/` + `static/` — Flask UI
- `src/config.md` — device/routine/theme definitions
- `data/` — sibling `orc_data` package (piper voice model + ephemeris)
