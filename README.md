# orc

Personal home automation orchestrator. Drives lights, Chromecast speakers,
an LG webOS TV, and an AC unit on a schedule built from sunrise/sunset,
calendar events, and a markdown config file.

## What it does

- Runs themed daily routines (e.g. *work day* / *day off*) with events tied to
  wall-clock times or sun position at a configured lat/long.
- Pulls calendar events from an iCal feed and schedules audio alerts /
  routines around them.
- Skips market-holiday rules via a configurable holidays endpoint.
- Controls Hubitat lights (MQTT/REST), Chromecast speakers (pychromecast +
  yt-dlp for YouTube audio), and an AC unit (BroadLink IR).
- Supports weather-condition triggers (e.g. `SUNNY`) via the open-meteo API;
  the schedule UI marks weather-triggered jobs with a ☀ badge.
- Via the optional `orc_plugins` package (`plugins/`): controls an LG webOS
  TV (aiowebostv + BroadLink IR), monitors YoLink leak sensors (fatal-level
  audio alert on water detection), and runs the entrance-sensor automation.
- Tracks network presence of configured devices/people and gates
  person-specific routine steps on who is currently home.
- Serves a small Flask UI for manual control, schedule inspection, theme
  override, and an activity log.

## Quick start — development, no hardware required

orc runs happily on a laptop with nothing attached: when `ORC_ENABLED` is
unset, every device and secret integration is stubbed out. Calls that would
touch hardware are printed to stderr as `[disabled] ...` instead, and the
whole UI works against the sample config.

You'll need:

- **Python 3.14+** (what CI and production use)
- **git LFS** — the TTS voice model, ephemeris, and compiled CSS are LFS
  objects; without it you'll get pointer files and confusing failures
- **PortAudio**, to build the `pyaudio` dependency:
  `brew install portaudio` (macOS) or
  `sudo apt-get install portaudio19-dev` (Debian/Ubuntu)
- **libpcap**, for packet capture (Debian/Ubuntu):
  `sudo apt-get install libpcap-dev`

Then:

```sh
git lfs install
git clone https://github.com/exussum/orc.git && cd orc

python3 -m venv ~/.venv-orc            # this exact path matters:
source ~/.venv-orc/bin/activate        # scripts/dev.sh sources it
pip install ./data '.[test]'

pytest && pytest plugins

PYTHONPATH=src:data/src:plugins/src python -c 'from orc.runner import flask; flask()'
```

Open <http://localhost:8000> — the scene, device, schedule, presence, and
log views are all live, driven by the sample config in `src/config.md`.
`PYTHONPATH=src:data/src:plugins/src` makes the dev server run your working
tree rather than the copy installed in the venv (the sample config registers
plugins from `plugins/src`, so it must be on the path).

Before your first commit, install the git hooks (black, isort, flake8,
opengrep, mypy, both test suites, and more run on every commit):

```sh
pre-commit install
```

## Installing it for real

Hardware and services — skip whatever you don't have; devices you leave out
of `config.md` are never touched:

- a Hubitat hub with the Maker API app enabled (lights)
- Chromecast speakers on the same LAN
- an LG webOS TV, plus a BroadLink IR blaster for power-on and AC control
- a YoLink hub with leak sensors
- a USB audio output on the machine running orc (spoken announcements)
- a Bitwarden Secrets Manager account holding the runtime secrets

Steps:

1. **Install** on the target machine (add `./plugins` if you want the
   bundled plugins — LG TV, YoLink, entrance sensor):

   ```sh
   pip install ./data . ./plugins
   ```

2. **Create a config directory** (e.g. `/etc/orc`) and copy `src/config.md`
   into it as a starting point. Devices, people, routines, themes, room
   configs, and plugins are all defined there — the sample file demonstrates
   every table schema. Per-plugin markdown goes in a `plugins/` subdirectory
   (see `src/plugins/` for examples).

3. **Create the secrets** in Bitwarden Secrets Manager (table below) and put
   a machine-account access token where the service can read it, e.g.
   `/etc/orc/bws_access_token`.

4. **Set the environment.** The minimum for a real installation:

   ```sh
   export ORC_ENABLED=1
   export ORC_HUBITAT_URL=http://<hubitat-host>/apps/api/<app-id>
   export ORC_CONFIG_DIR=/etc/orc
   export ORC_DB=sqlite:////var/lib/orc/jobs.sqlite
   export BWS_ACCESS_TOKEN=file:///etc/orc/bws_access_token
   ```

   See [Configuration](#configuration) for the full list (timezone,
   lat/long, audio device, …).

5. **Run `orc`** — installed as a console script, it starts gunicorn on
   `0.0.0.0:8000`. Use your process manager of choice to keep it up;
   production here runs it under supervisor.

## Configuration

Two config surfaces:

1. **Markdown config** at `ORC_CONFIG_DIR/config.md` (the in-repo sample is
   `src/config.md`). Defines devices, routines, themes, room configs, ad-hoc
   routines, plugins, and button highlights.
2. **Environment variables**:

   | Var                     | Purpose                                                          | Default                         |
   |-------------------------|------------------------------------------------------------------|---------------------------------|
   | `ORC_ENABLED`           | Opt-in: talk to real devices/secrets; unset = offline/dry-run    | unset                           |
   | `ORC_HUBITAT_URL`       | Hubitat Maker API base URL                                       | unset                           |
   | `ORC_CONFIG_DIR`        | Directory containing `config.md`                                 | `src`                           |
   | `ORC_DB`                | SQLAlchemy URL for the APScheduler / orc state DB                | `sqlite:////tmp/jobs.sqlite`    |
   | `ORC_TZ`                | IANA timezone                                                    | `America/New_York`              |
   | `ORC_LAT`               | Latitude for sunrise/sunset                                      | `40.7143`                       |
   | `ORC_LONG`              | Longitude for sunrise/sunset                                     | `-74.0060`                      |
   | `ORC_HTTP_TIMEOUT`      | Default outbound HTTP timeout (s)                                | `5`                             |
   | `ORC_HTTP_ICAL_TIMEOUT` | Timeout for the iCal fetch (s)                                   | `120`                           |
   | `ORC_ROOT_DOMAIN`       | Suffix stripped from presence hostnames; allowlisted for streams | `example.test`                  |
   | `ORC_INTERNAL_URL`      | LAN-reachable base URL for static audio; its host is allowlisted | `http://example.test`           |
   | `ORC_AUDIO_DEVICE`      | Substring matching the audio output device for TTS/alerts        | `""`                            |
   | `ORC_BROADLINK_CODES`   | Path to BroadLink IR codes JSON                                  | `/etc/orc/broadlink_codes.json` |
   | `BWS_ACCESS_TOKEN`      | URL whose body is the Bitwarden access token                     | required if `ORC_ENABLED`       |

   `BWS_ACCESS_TOKEN` is a URL (e.g. `data:` or `file://`), not the value
   itself — the body of the URL is read at startup.

## Secrets (Bitwarden)

When `ORC_ENABLED` is set, secrets are pulled from Bitwarden Secrets
Manager by name. The first three are required — startup fails without them;
the YoLink pair is optional and only read by the yolink plugin:

| Key                    | Used for                                           |
|------------------------|----------------------------------------------------|
| `HUBITAT_ACCESS_TOKEN` | Hubitat Maker API access token (appended as query) |
| `MARKET_HOLIDAYS_URL`  | JSON endpoint returning market holiday dates       |
| `ICS_URL`              | iCal feed URL for calendar-driven routines         |
| `YOLINK_ID`            | Yolink API client ID (optional)                    |
| `YOLINK_SECRET`        | Yolink API client secret (optional)                |

## Running

Two entry points in `src/orc/runner.py`, both serving on `0.0.0.0:8000`:

- `web()` — gunicorn; this is what the `orc` console script runs (production)
- `flask()` — Flask's dev server (development)

## Deploy

This is the author's deploy flow — it targets a private package registry, so
if you're installing elsewhere, use [Installing it for real](#installing-it-for-real)
instead.

`sh scripts/upload.sh` builds and publishes to the internal package registry.
Pass `full` to also publish the `orc_data` sub-package:

```sh
sh scripts/upload.sh full
```

`sh scripts/build-and-install.sh` runs `upload.sh` then SSHs to the target host and
runs `install.sh`, which syncs dependencies, reinstalls from the registry, and
bounces the `orc` supervisor job.

## Layout

- `src/orc/__init__.py` — `Config` (env vars + markdown-config loader)
- `src/orc/runner.py` — Flask + APScheduler entry points (`web`, `flask`)
- `src/orc/api.py` — schedule construction, rule routing, `SnapshotManager`, context-injecting executor
- `src/orc/model.py` — state constants (`ON`, `OFF`, `STOP`, …), markdown → config parsing, routine/theme types
- `src/orc/collections.py` — markdown table parsing (`doc_to_table`, `doc_to_sub_tables`, `LockedDict`)
- `src/orc/dal/` — integrations split by target: `mqtt.py` (Hubitat MQTT
  device cache), `hubitat.py` (Hubitat Maker API), `chromecast.py`,
  `feeds.py` (iCal / market holidays / open-meteo weather), `bws.py`
  (Bitwarden), `usb.py` (pyaudio + piper TTS), `broadlink.py` (IR),
  `net.py` (presence scanning), `sqlite.py`
- `src/orc/decorators.py` — shared decorators and locks: `requires_ctx`, `requires_enabled`, `plugin_config`, `synchronized`, `audio_lock`, `silence_fd`
- `src/orc/declarations.py` — per-config-load plugin declaration collection, built into the device/plugin `Registry`
- `src/orc/plugins.py` — built-in plugin functions (`light_test`, `rebuild_jobs`, `reboot`, `reboot_hubitat`, `sound_test`, `back_on_schedule`)
- `src/orc/security.py` — `safe_eval` for config expressions, URL allowlisting, HTML sanitizing
- `src/orc/_build.py` — build SHA/time stamped at release
- `src/orc/locale.py` — log-message string constants
- `src/orc/view.py` + `templates/` + `static/` — Flask UI (schedule, device, presence, log, config views)
- `src/config.md` — sample device/routine/theme/plugin definitions
- `src/plugins/` — per-plugin markdown config files
- `data/` — sibling `orc_data` package (piper voice model + ephemeris)
- `plugins/` — optional `orc_plugins` plugin package (e.g. `entrance_sensor`) with its own tests
