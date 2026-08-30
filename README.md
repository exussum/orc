# orc

Personal home automation orchestrator. Drives lights, Chromecast speakers,
an LG webOS TV, and an AC unit on a schedule built from sunrise/sunset,
calendar events, and a line-based config file.

## What it does

- Runs themed daily routines (for example, *work day* / *day off*) with
  events tied to wall-clock times or sun position at a configured lat/long.
- Skips market-holiday rules using a configurable holidays endpoint.
- Controls Hubitat lights (MQTT/REST), Chromecast speakers (pychromecast +
  yt-dlp for YouTube audio), and an AC unit (BroadLink IR).
- Supports weather-condition triggers (for example, `SUNNY`) through the
  open-meteo API; the schedule UI marks weather-triggered jobs with a ☀ badge.
- Via the optional `orc_extras` package (`extras/`): pulls calendar events
  from an iCal feed and schedules alerts/routines around them, controls an
  LG webOS TV (aiowebostv + BroadLink IR), monitors YoLink leak sensors
  (fatal-level audio alert on water detection), and runs the
  entrance-sensor automation.
- Tracks network presence of configured devices/people and gates
  person-specific routine steps on who is currently home.
- Serves a small Flask UI for manual control, schedule inspection, theme
  override, and an activity log.

## Quick start — development, no hardware required

orc runs happily on a laptop with nothing attached: the sample config's
`provider` lines name the stub backends (`orc.dal.<capability>.stub`), so
every device and secret integration is faked in memory and the whole UI
works. A real installation's config names the real backends instead
(for example, `provider mqtt orc.dal.mqtt.hubitat`) — though `secrets`,
`hubitat`, `mqtt`, `chromecast`, and `audio` default to their real backend
when the `provider` line is omitted entirely, so a production config only
needs to name `weather`, `holiday`, and `blaster` explicitly. An explicit
`provider` line, stub or real, always overrides the default.

You'll need:

- **Python 3.14+** (what CI and production use)
- **git LFS** — the TTS voice model, ephemeris, and compiled CSS are LFS
  objects; without it you'll get pointer files and confusing failures.
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

pytest && pytest extras

PYTHONPATH=src:data/src:extras/src python -c 'from orc.runner import flask; flask()'
```

Open <http://localhost:8000> — the scene, device, schedule, presence, and
log views are all live, driven by the sample config in `src/config.orc`.
`PYTHONPATH=src:data/src:extras/src` makes the dev server run your working
tree rather than the copy installed in the venv (the sample config registers
plugins from `extras/src`, so it must be on the path).

Before your first commit, install the git hooks (black, isort, flake8,
opengrep, mypy, both test suites, and more run on every commit):

```sh
pre-commit install
```

## Install it for real

Hardware and services — skip whatever you don't have; devices you leave out
of `config.orc` are never touched:

- a Hubitat hub with the Maker API app enabled (lights)
- Chromecast speakers on the same LAN
- an LG webOS TV, plus a BroadLink IR blaster for power-on and AC control
- a YoLink hub with leak sensors
- a USB audio output on the machine running orc (spoken announcements)
- a Bitwarden Secrets Manager account holding the runtime secrets

Steps:

1. **Install it on the target machine.** Add `./extras` if you want the
   bundled plugins — LG TV, YoLink, entrance sensor. The `command-cfg`
   config parser resolves from the internal package registry, same as the
   deploy flow:

   ```sh
   pip install ./data . ./extras --extra-index-url "$ORC_REGISTRY_URL"
   ```

2. **Create a config directory, for example `/etc/orc`.** Copy
   `src/config.orc` into it as a starting point. Devices, people, routines,
   themes, room configs, and plugins are all defined there — the sample
   file demonstrates every command. Per-plugin configs go in a `plugins/`
   subdirectory (see `src/plugins/` for examples).

3. **Create the secrets in Bitwarden Secrets Manager.** See
   [Secrets (Bitwarden)](#secrets-bitwarden), and put a machine-account
   access token where the service can read it — for example,
   `/etc/orc/bws_access_token`.

4. **Set the environment.** Two variables:

   ```sh
   export ORC_CONFIG_DIR=/etc/orc
   export BWS_ACCESS_TOKEN=file:///etc/orc/bws_access_token
   ```

   Everything else (URLs, DB path, timezone, coordinates, audio device, …)
   is a `setting` line in `config.orc` — see [Configuration](#configuration).

5. **Run `orc`.** It's installed as a console script and starts `gunicorn`
   on `0.0.0.0:<port>` (the `port` setting, default 8000). Use your process
   manager of choice to keep it up; production here runs it under
   `supervisor`.

## Configuration

Two config surfaces:

1. **Line-based config** at `ORC_CONFIG_DIR/config.orc` (the in-repo sample
   is `src/config.orc`). Defines settings, devices, people, routines, themes,
   room configs, ad-hoc routines, plugins, button highlights, and the secrets
   and weather providers. One command per
   line with shell-style quoting and `#` comments; a `.` repeats the token in
   the same position on the line above. The grammar lives in
   `orc.loader.GRAMMAR` and is parsed by the `command-cfg` package:

   ```
   setting base_url http://orc.internal.example

   device define Light
   device add    Light BEDROOM_LAMP 'bedroom lamp' --room Bedroom
   device seal   Light

   routine define ROUTINE_RESET Reset
   routine append ROUTINE_RESET Light off --trigger SYSTEM

   theme 'work day' ROUTINE_RESET 1:00
   ```

   `setting` lines fill `orc.model.Settings` (exposed as `config.settings`).
   The required keys fail startup with a named `ConfigError` when a line is
   missing or its value is empty:

   | Setting           | Purpose                                                          |
   |-------------------|-------------------------------------------------------------------|
   | `base_url`        | LAN-reachable base URL for static audio; its host is allowlisted for streams |
   | `lan_domain`      | Suffix stripped from presence hostnames; subdomains allowlisted for streams |
   | `jobs_db`         | SQLAlchemy URL for the APScheduler / orc state DB                |
   | `lat` / `long`    | Coordinates for sunrise/sunset                                   |
   | `audio_device`    | Substring matching the audio output device for TTS/alerts        |
   | `broadlink_codes` | Path to BroadLink IR codes JSON                                  |
   | `mqtt_host`       | Broker host for the Hubitat MQTT export                          |

   The optional keys default when omitted:

   | Setting             | Purpose                            | Default                 |
   |---------------------|-------------------------------------|-------------------------|
   | `tz`                | IANA timezone                      | `America/New_York`      |
   | `hubitat_url`       | Hubitat Maker API base URL         | `http://hubitat.example`|
   | `http_timeout`      | Default outbound HTTP timeout (s)  | `5`                     |
   | `port`              | HTTP listen port                   | `8000`                  |

2. **Environment variables** — only the bootstrap pair that can't live in
   the config file:

   | Var                | Purpose                                      | Default                           |
   |--------------------|-----------------------------------------------|-----------------------------------|
   | `ORC_CONFIG_DIR`   | Directory containing `config.orc`            | `src`                             |
   | `BWS_ACCESS_TOKEN` | URL whose body is the Bitwarden access token | required by `orc.dal.secrets.bws` |

   `BWS_ACCESS_TOKEN` is a URL (for example, `data:` or `file://`), not the value
   itself — the body of the URL is read at startup.

## Secrets (Bitwarden)

With the default `secrets` provider (`orc.dal.secrets.bws`), secrets are
pulled from Bitwarden Secrets Manager by name. The first two are
required — startup fails without them; the rest are optional: the MQTT
pair credentials the Hubitat MQTT connection. Any other key is read on
demand by whichever plugin config names it (for example,
`YOLINK_ID`/`YOLINK_SECRET` for the yolink plugin, or a calendar feed's
secret):

| Key                    | Used for                                           |
|------------------------|-----------------------------------------------------|
| `HUBITAT_ACCESS_TOKEN` | Hubitat Maker API access token (appended as query) |
| `MARKET_HOLIDAYS_URL`  | JSON endpoint returning market holiday dates       |
| `MQTT_USER`            | Hubitat MQTT broker username (optional)            |
| `MQTT_PASSWORD`        | Hubitat MQTT broker password (optional)            |

## Running

Two entry points in `src/orc/runner.py`, both serving on `0.0.0.0:<port>`
(the `port` setting, default 8000):

- `web()` — gunicorn; this is what the `orc` console script runs (production)
- `flask()` — Flask's dev server (development)

## Deploy

This is the author's deploy flow — it targets a private package registry, so
if you're installing elsewhere, use [Install it for real](#install-it-for-real)
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

- `src/orc/__init__.py` — `Config` (`.orc` config loading and installation)
- `src/orc/loader.py` — the config grammar, `parse_config`/`validate`, the `Cast` value coercions, and plugin config loading, all on `command-cfg`
- `src/orc/runner.py` — Flask + APScheduler entry points (`web`, `flask`)
- `src/orc/api.py` — schedule construction, rule routing, `SnapshotManager`, context-injecting executor
- `src/orc/model.py` — state constants (`ON`, `OFF`, `STOP`, …), state/time parsing (`resolve_state`, `resolve_time`), routine/theme/device types
- `src/orc/collections.py` — `LockedDict` and `where`
- `src/orc/dal/` — integrations split by capability, each a package with a
  real backend plus a `stub.py` for development: `mqtt/` (Hubitat MQTT
  device cache), `hubitat/` (Hubitat Maker API), `chromecast/`,
  `holiday/` (market holidays), `weather/` (open-meteo), `blaster/`
  (BroadLink IR), `secrets/` (Bitwarden). Plus `audio.py` (pyaudio + piper
  TTS), `net.py` (presence scanning), `scheduler.py`, `sqlite.py`,
  `interfaces.py` (the `Provider` capability contracts)
- `src/orc/decorators.py` — shared decorators and locks: `requires_ctx`, `synchronized`, `audio_lock`, `silence_fd`
- `src/orc/declarations.py` — per-config-load plugin declaration collection, built into the device/plugin `Registry`
- `src/orc/plugins.py` — built-in plugin functions (`light_test`, `rebuild_jobs`, `reboot`, `reboot_hubitat`, `sound_test`, `back_on_schedule`)
- `src/orc/security.py` — `safe_eval` for config expressions, URL allowlisting
- `src/orc/_build.py` — build SHA/time stamped at release
- `src/orc/locale.py` — log-message string constants
- `src/orc/view.py` + `templates/` + `static/` — Flask UI (schedule, device, presence, log, config views)
- `src/config.orc` — sample device/routine/theme/plugin definitions
- `src/plugins/` — per-plugin config files
- `data/` — sibling `orc_data` package (piper voice model + ephemeris)
- `extras/` — optional `orc_extras` plugin package (for example, `entrance_sensor`) with its own tests
