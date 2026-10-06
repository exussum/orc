# orc

Personal home automation orchestrator.

## What it does

orc reads one config file and turns it into each day's schedule. A day
gets a theme, such as *work day* or *day off*, chosen by weekday and
market holidays. A theme is a list of routines pinned to clock times or to
sunrise and sunset. A routine is a list of device commands, each of which
can wait for a particular person to be home or for the weather to match.

Out of the box:

- Hubitat lights, over MQTT
- Chromecast speakers, including YouTube audio and spoken announcements
- a USB speaker on the orc machine, for announcements and alerts
- presence, by probing the LAN for phones and, optionally, listening for
  Google Find Hub BLE tags

A small web UI shows the schedule, the devices, who is home, and the log,
and lets you run scenes and override the day's theme by hand. Alerts can
also reach your phone by Web Push.

Optional plugins in `extras/`: calendar, entrance_sensor, lg_ac, lg_tv,
react, travel, yolink.

## Install

Before you start you need 1) a Bitwarden Secrets Manager account and 2) an
MQTT broker, with the Hubitat MQTT Export app pointed at it (Maker API only
for the reboot button). Everything else is optional; a device that isn't in
`config.orc` is never touched.

1. **Install the packages.** `command-cfg` comes from the internal package
   registry.

   ```sh
   pip install ./data . --extra-index-url "$ORC_REGISTRY_URL"
   ```

   The plugins are a separate package, `./extras`, installed as one:

   ```sh
   pip install ./extras
   ```

   Each plugin's README covers its config, secrets and `plugin` line; a
   plugin that no `plugin` line names is never imported.

   - [calendar](https://github.com/exussum/orc/tree/main/extras/src/orc_extras/calendar) — iCal feed events that schedule alerts and routines
   - [entrance_sensor](https://github.com/exussum/orc/tree/main/extras/src/orc_extras/entrance_sensor) — front-door motion automation
   - [lg_ac](https://github.com/exussum/orc/tree/main/extras/src/orc_extras/lg_ac) — LG window AC over local ThinQ2
   - [lg_tv](https://github.com/exussum/orc/tree/main/extras/src/orc_extras/lg_tv) — LG webOS TV with BroadLink IR power-on
   - [react](https://github.com/exussum/orc/tree/main/extras/src/orc_extras/react) — rules that fire on device events and sensor measurements
   - [travel](https://github.com/exussum/orc/tree/main/extras/src/orc_extras/travel) — drive and flight time checks ahead of calendar travel
   - [yolink](https://github.com/exussum/orc/tree/main/extras/src/orc_extras/yolink) — YoLink leak sensors

2. **Create a config directory**, such as `/etc/orc`, and copy
   [`src/config.orc`](https://github.com/exussum/orc/blob/main/src/config.orc) into it. Devices, people, routines, themes, rooms, and
   plugins are all defined there. The sample covers every command except
   `person` and `tag`, left out so a stub-backed dev run never attempts a
   privileged presence scan. Plugin configs go in a `plugins/` subdirectory;
   samples are in `examples/configs/`.

3. **Create the secrets** in Bitwarden Secrets Manager (see
   [Secrets](#secrets-bitwarden)) and put a machine-account access token
   where the service can read it, for example `/etc/orc/bws_access_token`.

4. **Set two environment variables.**

   ```sh
   export ORC_CONFIG_DIR=/etc/orc
   export BWS_ACCESS_TOKEN=file:///etc/orc/bws_access_token
   ```

   Everything else is a `setting` line in `config.orc`; see
   [Configuration](#configuration).

5. **Run `orc`.** The console script starts gunicorn on `0.0.0.0:<port>`
   (the `port` setting, default 8000). Keep it up with the process manager
   of your choice; production here uses supervisor.

## Configuration

Two config surfaces:

1. **Line-based config** at `ORC_CONFIG_DIR/config.orc` (the in-repo sample
   is [`src/config.orc`](https://github.com/exussum/orc/blob/main/src/config.orc)). Defines settings, devices, people, routines, themes,
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

   When a routine fires, its commands run grouped by device type.
   `--sort=<n>` on a `device define` or `device only` line sets that type's
   place in the order, lowest first. Types without one run last.

   A `USB` device's target is the audio device's USB serial number (Linux
   only); `orc-audio-devices` lists each card's index, serial, and
   ALSA/PortAudio names.

   `setting` lines fill `orc.model.Settings` (exposed as `config.settings`).
   The required keys fail startup with a named `ConfigError` when a line is
   missing or its value is empty:

   | Setting                                                    | Purpose                                                                                                                                                                               |
   | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
   | `base_url`                                                 | LAN-reachable base URL Chromecasts fetch alert media from; its host marks UI requests as internal, and it is the VAPID contact claim, so Web Push needs `https://<host>` with no port |
   | `lan_domain`                                               | Suffix stripped from presence-page hostnames                                                                                                                                          |
   | `jobs_db`                                                  | SQLAlchemy URL for the APScheduler / orc state DB                                                                                                                                     |
   | `lat` / `long`                                             | Coordinates for sunrise/sunset                                                                                                                                                        |
   | `warning_device` / `attention_device` / `emergency_device` | `USB.*`/`Chromecast.*` device for each Alarm severity's TTS/alerts                                                                                                                    |
   | `broadlink_codes`                                          | Path to BroadLink IR codes JSON                                                                                                                                                       |
   | `mqtt_host`                                                | Broker host for the Hubitat MQTT export                                                                                                                                               |

   The optional keys default when omitted:

   | Setting             | Purpose                                        | Default                  |
   | ------------------- | ---------------------------------------------- | ------------------------ |
   | `tz`                | IANA timezone                                  | `America/New_York`       |
   | `hubitat_url`       | Hubitat Maker API base URL                     | `http://hubitat.example` |
   | `http_timeout`      | Default outbound HTTP timeout (s)              | `5`                      |
   | `port`              | HTTP listen port                               | `8000`                   |
   | `presence_hours`    | How long a presence detection persists         | `9`                      |
   | `checkin_hours`     | How long a manual check-in persists            | `1`                      |
   | `sunset_lead_hours` | Hours before sunset that sunset routines fire  | `1`                      |

2. **Environment variables** — only the bootstrap pair that can't live in
   the config file:

   | Var                | Purpose                                      | Default                           |
   | ------------------ | -------------------------------------------- | --------------------------------- |
   | `ORC_CONFIG_DIR`   | Directory containing `config.orc`            | `src`                             |
   | `BWS_ACCESS_TOKEN` | URL whose body is the Bitwarden access token | required by `orc.dal.secrets.bws` |

   `BWS_ACCESS_TOKEN` is a URL (for example, `data:` or `file://`), not the value
   itself — the body of the URL is read at startup.

   To check a vault from another machine, `orc-secrets` loads the config to
   learn which secrets it needs, fetches through the config's secrets
   provider, and runs the same check startup does: one line per declared
   secret, `ok` or `bad:` with the reason, then any vault entry nothing
   declared. A bad row makes the command exit nonzero. For this command
   alone, `BWS_ACCESS_TOKEN` is the token itself, not a URL:

   ```sh
   env BWS_ACCESS_TOKEN=0.abc... orc-secrets /etc/orc/config.orc
   ```

## Built-in plugins

Three listeners on the Hubitat MQTT feed ship inside `orc` and are turned
on the same way as any plugin, by a `plugin` line. The sample config
carries all three; drop a line and that listener is off.

```
plugin Buttons  orc.plugins.buttons
plugin Battery  orc.plugins.battery
plugin External orc.plugins.external
```

- **Buttons** runs an ad hoc routine when a remote is pressed. Each
  `remote <device> <button> <event> <action>` line maps one button and
  event (`pushed`, `held`, ...) on a `Button` device to an ad hoc name; a
  press with no matching line is ignored, and a line naming an unknown
  action logs and alerts. The log attributes the run to the remote.

  ```
  remote Button.LIVING_ROOM_REMOTE 1 pushed 'All Lights On'
  remote .                         1 held   Silence
  ```

- **Battery** watches every device's `battery` attribute and logs
  ``Low battery on `<device>` (CRITICAL)`` with a phone notification once,
  when the level first drops to 10% or below.

- **External** logs every device change orc didn't command, as
  ``<device> <attribute>: <old> → <new>`` under the External source. A wall
  switch or the Hubitat app flipping a light shows up here; nothing is
  reverted.

## Secrets (Bitwarden)

With the default `secrets` provider (`orc.dal.secrets.bws`), secrets are
pulled from Bitwarden Secrets Manager by name. Each consumer declares the
secrets it reads and the shape each must have: a real provider backend
through a `REQUIRED_SECRETS` constant, a plugin through the `secrets=` argument of
its `declare()` hook, and a `tag` line through its named EIK. Startup
checks every declared secret before any plugin's `setup()` runs and fails
with the full list of problems, so what is required follows the providers
and plugins the config selects; a stub provider declares nothing. A blank
value counts as missing wherever a secret is read.

| Key                    | Declared by               | Shape                                 |
| ---------------------- | ------------------------- | ------------------------------------- |
| `HUBITAT_ACCESS_TOKEN` | `orc.dal.hubitat.http`    | UUID (Hubitat Maker API access token) |
| `MARKET_HOLIDAYS_URL`  | `orc.dal.holiday.polygon` | http(s) URL returning market holidays |
| `VAPID_PRIVATE_KEY`    | `orc.dal.push.webpush`    | base64url 32-byte EC key              |
| `MQTT_USER`            | nobody (optional)         | Hubitat MQTT broker username          |
| `MQTT_PASSWORD`        | nobody (optional)         | Hubitat MQTT broker password          |
| a `tag` line's secret  | the `tag` line            | 32-byte hex EIK                       |
| plugin secrets         | the plugin's `declare`    | see the plugin's README               |

## Phone notifications (Web Push)

Log lines that orc flags as worth a notification — a failed dispatch, a
presence scan error, an unknown button, a leak sensor firing — are also
pushed to every phone or browser that has opted in, through the
browser's own push service (Mozilla for Firefox, FCM for Chrome, Apple
for Safari). There is no third-party account: orc signs each push with a
VAPID key and posts it straight to the subscription's endpoint.

Signing uses [py-vapid](https://pypi.org/project/py-vapid/), with
`base_url` as the contact claim, so `base_url` must be a plain
`https://<host>` with no port or every push fails before it is sent.

1. Generate a key once and store it in the secrets provider as
   `VAPID_PRIVATE_KEY`:

   ```sh
   orc-vapid-key
   ```

2. Open the System page on the device and press **Enable notifications**.
   The browser asks for notification permission, registers orc's service
   worker, and hands orc its subscription, and orc answers with a
   confirmation notification; opening the System page again later re-sends
   the subscription silently, which is how a device recovers after
   `jobs_db` is lost. **Disable notifications** drops the device from
   orc's table and releases the browser's subscription.

## BLE tag presence (Find Hub)

Alongside the LAN probe, orc can track Google Find Hub (FMDN) BLE tags — for
example, a Chipolo POP. A `tag` line binds a tag to a person:

```
person Alice alices-phone.example aa:bb:cc:dd:ee:ff
tag    Alice EIK_ALICE 2026-09-22T13:48:37+00:00
```

Pair the tag with Google Find Hub on an Android phone, then export its
identity key and pair date with GoogleFindMyTools. The key goes in the
secrets provider under the name the `tag` line references (`EIK_ALICE`
above), as 32 bytes of hex; the pair date goes in the line as ISO 8601.

Matching is fully local: a background listener computes the tag's rotating
ephemeral ID (EID) from the key and pair date and hears every advertisement
the tag sends, so every hearing updates the person's presence directly
instead of gambling on a scan window. After the one-time key export, nothing
talks to Google.

The EID scheme is Google's public
[Find Hub Network accessory spec](https://developers.google.com/nearby/fast-pair/specifications/extensions/fmdn).
The variant semantics and golden test vectors follow **BSkando**'s
**GoogleFindMy-HA** (MIT) — https://github.com/BSkando/GoogleFindMy-HA — and
identity keys are exported with **leonboe1**'s **GoogleFindMyTools** —
https://github.com/leonboe1/GoogleFindMyTools.

## Running

Two entry points in `src/orc/runner.py`, both serving on `0.0.0.0:<port>`
(the `port` setting, default 8000):

- `web()` — gunicorn; this is what the `orc` console script runs (production)
- `flask()` — Flask's dev server (development)

## Deploy

This is the author's deploy flow — it targets a private package registry, so
if you're installing elsewhere, use [Install](#install)
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

- `src/` — the `orc` package and the sample `config.orc` the dev server runs against
- `extras/` — optional `orc_extras` plugin package with its own tests
- `data/` — sibling `orc_data` package (piper voice model + ephemeris)
- `examples/` — copyable per-plugin config samples and an example plugin
- `scripts/` — build, publish, and install scripts
- `tests/` — the core test suite

## Quick start — development, no hardware required

orc runs happily on a laptop with nothing attached: the sample config's
`provider` lines name the stub backends (`orc.dal.<capability>.stub`), so
every device and secret integration is faked in memory and the whole UI
works. A real installation's config names the real backends instead
(for example, `provider adapter orc.dal.mqtt.hubitat`) — though `secrets`,
`hubitat`, `adapter`, `chromecast`, `audio`, and `push` default to their real
backend when the `provider` line is omitted entirely, so a production config
only needs to name `weather`, `holiday`, and `blaster` explicitly. An
explicit `provider` line, stub or real, always overrides the default. The
MQTT broker connection itself is not a provider: it is always
`orc.dal.mqtt.paho`, and tests patch its functions directly.

You'll need:

- **uv** — manages the venv, Python 3.14 (what CI and production use), and
  all dependencies
- **git LFS** — the TTS voice model and ephemeris are LFS objects; without
  it you'll get pointer files and confusing failures.
- **PortAudio**, to build the `pyaudio` dependency:
  `brew install portaudio` (macOS) or
  `sudo apt-get install portaudio19-dev` (Debian/Ubuntu)
- **libpcap**, for packet capture (Debian/Ubuntu):
  `sudo apt-get install libpcap-dev`

Then:

```sh
git lfs install
git clone https://github.com/exussum/orc.git && cd orc

uv sync --extra test --extra lint

uv run pytest && uv run pytest extras

uv run orc-dev
```

Open <http://localhost:8000> — the scene, device, schedule, presence, and
log views are all live, driven by the sample config in [`src/config.orc`](https://github.com/exussum/orc/blob/main/src/config.orc)
(the `ORC_CONFIG_DIR` default). `uv sync` installs `orc` and `orc_extras`
editable, so the dev server runs your working tree — the sample config's
plugin lines resolve against `extras/src` directly.

Before your first commit, install the git hooks (ruff, opengrep, mypy,
both test suites, and more run on every commit):

```sh
pre-commit install
```
