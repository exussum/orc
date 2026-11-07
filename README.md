# orc

orc is a home automation orchestrator for one house. You describe your
devices, the people who live there, and the day's routines in one text
file; orc turns that into a schedule, runs it, and shows you what it did.
It's small enough to run on a Raspberry Pi and drives Hubitat lights,
Chromecast speakers, and a USB speaker, with optional plugins for an LG AC
and TV, leak sensors, calendars, and travel.

This README is for someone setting orc up or working on it. You don't need
any hardware to try it.

## Quick start

orc runs on a laptop with nothing attached. The sample config names a stub
backend for every device and secret, so the whole UI works against fake
devices.

You need:

- **uv**, which manages the virtual environment, Python 3.14, and the
  dependencies.
- **Git LFS**. The speech voice and the ephemeris are LFS objects; without
  it you get pointer files and confusing failures.
- **PortAudio**, to build the `pyaudio` dependency: `brew install portaudio`
  on macOS, or `sudo apt-get install portaudio19-dev` on Debian and Ubuntu.
- **libpcap**, for packet capture, on Debian and Ubuntu:
  `sudo apt-get install libpcap-dev`.
- **tailwindcss**, the standalone CLI, which `orc-dev` runs to build the
  stylesheet: `brew install tailwindcss` on macOS, or the binary from
  [Tailwind's releases](https://github.com/tailwindlabs/tailwindcss/releases)
  on your `PATH`.

Then:

```sh
git lfs install
git clone https://github.com/exussum/orc.git && cd orc
uv sync --extra test --extra lint
uv run orc-dev
```

Open <http://localhost:8000>. The scene, device, schedule, presence, and
log pages are live, driven by the sample config in
[`src/config.orc`](src/config.orc). To run the tests, use
`uv run pytest && uv run pytest extras`.

## How it works

Everything starts from one config file, `config.orc`. These are the words
you'll meet:

- A **device** is something orc can command or read: a `Light`, a
  `Chromecast`, a `Sensor`.
- A **routine** is a list of device commands. Each command can wait for a
  person to be home or for the weather to match.
- A **theme** is a day plan: routines pinned to clock times, sunrise, or
  sunset. orc picks the day's theme by weekday and market holidays, *work
  day* or *day off*, unless you override it from the UI.
- An **ad hoc** is a routine you run by hand, from a page button, a remote,
  or a Stream Deck.
- A **provider** is the backend for one capability: the MQTT broker,
  Chromecast control, the weather service, secrets. Each has a real backend
  and a stub, chosen by a `provider` line; the stubs are what the quick
  start runs on.
- A **plugin** is an optional package turned on by a `plugin` line. The ones
  in `extras/` add a calendar, an entrance sensor, an LG AC and TV,
  event-driven rules, travel checks, and leak sensors.

At the start of each day orc builds the day's jobs from the theme and runs
them. The web UI shows the schedule, the devices and their state, who is
home, and a log of everything orc did and why; from it you can run scenes,
change the theme, and check in. Alerts can also reach your phone by Web
Push.

## Install

To run orc against real devices, you need the following:

- A host with the same packages as the quick start: uv, Git LFS, PortAudio,
  and libpcap.
- An MQTT broker, with the Hubitat MQTT Export app pointed at it. A house
  with no Hubitat names `provider adapter orc.dal.mqtt.none` instead and
  has no `Light`, `Button`, or `Sensor` devices.
- A secrets provider: a Bitwarden Secrets Manager account, which is the
  default, or a KeePass file. See [Secrets](#secrets-bitwarden).

Everything else is optional; a device that isn't in `config.orc` is never
touched.

1. **Install the packages.** Clone the repo and let uv build the
   environment. `command-cfg`, `orc-engine`, and `orc-queue` are pinned to
   GitHub tags in `pyproject.toml`, which uv resolves and pip doesn't.

   ```sh
   git lfs install
   git clone https://github.com/exussum/orc.git && cd orc
   uv sync
   ```

   This installs `orc`, the `orc_data` package of voice and ephemeris
   files, and the `orc_extras` plugins into `.venv`. A plugin that no
   `plugin` line names is never imported. Each plugin's README covers its
   config, secrets, and `plugin` line:

   - [calendar](extras/src/orc_extras/calendar): iCal feed events that schedule alerts and routines
   - [entrance_sensor](extras/src/orc_extras/entrance_sensor): front-door motion automation
   - [lg_ac](extras/src/orc_extras/lg_ac): LG window AC over local ThinQ2
   - [lg_tv](extras/src/orc_extras/lg_tv): LG webOS TV with BroadLink IR power-on
   - [react](extras/src/orc_extras/react): rules that fire on device events and sensor measurements
   - [travel](extras/src/orc_extras/travel): drive and flight time checks ahead of calendar travel
   - [yolink](extras/src/orc_extras/yolink): YoLink leak sensors

2. **Create a config directory**, such as `/etc/orc`, and copy
   [`src/config.orc`](src/config.orc) into it. Devices, people, routines,
   themes, rooms, and plugins are all defined there. The sample covers every
   command except `person` and `tag`, left out so a stub-backed dev run
   never attempts a privileged presence scan. Plugin configs go in a
   `plugins/` subdirectory; samples are in `examples/configs/`.

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

5. **Run `orc`.** The `.venv/bin/orc` script starts gunicorn on
   `0.0.0.0:<port>` (the `port` setting, default 8000). Keep it up with the
   process manager of your choice; production here uses supervisor.

## Configuration

Two config surfaces:

1. **Line-based config** at `ORC_CONFIG_DIR/config.orc` (the in-repo sample
   is [`src/config.orc`](src/config.orc)). Defines settings, devices,
   people, routines, themes, room configs, ad-hoc routines, plugins, button
   highlights, and the secrets and weather providers. One command per line
   with shell-style quoting and `#` comments; a `.` repeats the token in the
   same position on the line above. The grammar lives in
   `orc.kernel.loader.GRAMMAR` and is parsed by the `command-cfg` package:

   ```
   setting base_url http://orc.internal.example

   device define Light
   device add    Light BEDROOM_LAMP 'bedroom lamp' --room Bedroom
   device seal   Light

   routine define ROUTINE_RESET Reset
   routine append ROUTINE_RESET Light off --trigger-person SYSTEM

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
   | `jobs_db`                                                  | Path of the sqlite file holding orc's state                                                                                                                                           |
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

2. **Environment variables**, only the bootstrap pair that can't live in
   the config file:

   | Var                | Purpose                                      | Default                           |
   | ------------------ | -------------------------------------------- | --------------------------------- |
   | `ORC_CONFIG_DIR`   | Directory containing `config.orc`            | `src`                             |
   | `BWS_ACCESS_TOKEN` | URL whose body is the Bitwarden access token | required by `orc.dal.secrets.bws` |

   `BWS_ACCESS_TOKEN` is a URL (for example, `data:` or `file://`), not the
   value itself; the body of the URL is read at startup.

   To check a vault from another machine, `orc-secrets` loads the config to
   learn which secrets it needs, fetches through the config's secrets
   provider, and runs the same check startup does: one line per declared
   secret, `ok` or `bad:` with the reason, then any vault entry nothing
   declared. A bad row makes the command exit nonzero. For this command
   alone, `BWS_ACCESS_TOKEN` is the token itself, not a URL:

   ```sh
   env BWS_ACCESS_TOKEN=0.abc... orc-secrets /etc/orc/config.orc
   ```

## Secrets (Bitwarden)

With the default `secrets` provider (`orc.dal.secrets.bws`), secrets are
pulled from Bitwarden Secrets Manager by name. Each consumer declares the
secrets it reads and the shape each must have: a real provider backend
through a `REQUIRED_SECRETS` constant, a plugin through the `secrets=`
argument of its `declare()` hook. Startup checks every declared secret
before any plugin's `setup()` runs and fails with the full list of
problems, so what is required follows the providers and plugins the config
selects; a stub provider declares nothing. A blank value counts as missing
wherever a secret is read.

| Key                    | Declared by               | Shape                                 |
| ---------------------- | ------------------------- | ------------------------------------- |
| `MARKET_HOLIDAYS_URL`  | `orc.dal.holiday.polygon` | http(s) URL returning market holidays |
| `VAPID_PRIVATE_KEY`    | `orc.dal.push.webpush`    | base64url 32-byte EC key              |
| `MQTT_USER`            | nobody (optional)         | Hubitat MQTT broker username          |
| `MQTT_PASSWORD`        | nobody (optional)         | Hubitat MQTT broker password          |
| `FIND_HUB_USERNAME`    | `orc.dal.findhub.nova`    | the Find Hub account's Google address |
| `FIND_HUB_AAS_TOKEN`   | `orc.dal.findhub.nova`    | its long-lived Android auth token     |
| `FIND_HUB_ANDROID_ID`  | `orc.dal.findhub.nova`    | Android id the token was minted for   |
| `FIND_HUB_OWNER_KEY`   | `orc.dal.findhub.nova`    | 32-byte hex owner key                 |
| plugin secrets         | the plugin's `declare`    | see the plugin's README               |

### Secrets in a KeePass database (`orc.dal.secrets.keepass`)

`provider secrets orc.dal.secrets.keepass` reads the same names from
`secrets.kdbx` beside `config.orc` instead of Bitwarden: one entry per
secret, the name as the entry title and the value as its password. Any
KeePass app edits the file. Startup reads the master password from the
terminal with echo off, so orc has to be started by hand.

## Built-in plugins

Two listeners on the Hubitat MQTT feed and two System page buttons ship
inside `orc` and are turned on the same way as any plugin, by a `plugin`
line. The sample config carries all four; drop a line and that listener or
button is gone.

```
plugin Buttons                 orc.plugins.buttons
plugin Battery                 orc.plugins.battery
plugin 'Enable Notifications'  orc.plugins                 --section system --icon bell
plugin 'Disable Notifications' orc.plugins                 --section system --icon bell-slash
```

- **Buttons** runs an ad hoc routine when a remote is pressed. Each
  `remote <device> <button> <event> <action>` line maps one button and
  event (`pushed`, `held`, ...) on a `Button` device to an ad hoc id; a
  press with no matching line is ignored, and a line naming an unknown
  action logs and alerts. The log attributes the run to the remote.

  ```
  remote Button.LIVING_ROOM_REMOTE 1 pushed 'All Lights On'
  remote .                         1 held   Silence
  ```

- **Battery** watches every device's `battery` attribute and logs
  ``Low battery on `<device>` (CRITICAL)`` with a phone notification once,
  when the level first drops to 10% or below.
- **Enable Notifications** and **Disable Notifications** have no server
  side; a browser hook handles the press. See
  [Phone notifications](#phone-notifications-web-push).

Separately from these, orc itself logs every device change it didn't
command, as ``<device> <attribute>: <old> → <new>`` under the External
source. A wall switch or the Hubitat app flipping a light shows up here;
nothing is reverted.

## Phone notifications (Web Push)

Log lines that orc flags as worth a notification, such as a failed
dispatch, a presence scan error, an unknown button, or a leak sensor
firing, are also pushed to every phone or browser that has opted in,
through the browser's own push service (Mozilla for Firefox, FCM for
Chrome, Apple for Safari). There is no third-party account: orc signs each
push with a VAPID key and posts it straight to the subscription's
endpoint.

Signing uses [py-vapid](https://pypi.org/project/py-vapid/), with
`base_url` as the contact claim, so `base_url` must be a plain
`https://<host>` with no port or every push fails before it is sent.

1. Generate a key once and store it in the secrets provider as
   `VAPID_PRIVATE_KEY`:

   ```sh
   orc-vapid-key
   ```

2. Open the System page on the device and press **Enable Notifications**.
   The browser asks for notification permission, registers orc's service
   worker, and hands orc its subscription, and orc answers with a
   confirmation notification; opening the System page again later re-sends
   the subscription silently, which is how a device recovers after
   `jobs_db` is lost. **Disable Notifications** drops the device from
   orc's table and releases the browser's subscription. Both buttons are
   `plugin` lines; see [Built-in plugins](#built-in-plugins).

## BLE tag presence (Find Hub)

Alongside the LAN probe, orc can track Google Find Hub (FMDN) BLE tags, for
example a Chipolo POP. A `tag` line binds a tag to a person:

```
person Alice alices-phone.example aa:bb:cc:dd:ee:ff
tag    Alice 'Alice Keys'
```

The second word is the tag's name in the Find Hub app. Pair the tag with
Google Find Hub on an Android phone; orc reads its identity key and pair
date from the account through the `findhub` provider and keeps them in
`jobs_db`, so a boot never waits on Google. The account login comes from
four secrets, `FIND_HUB_USERNAME`, `FIND_HUB_AAS_TOKEN`,
`FIND_HUB_ANDROID_ID` and `FIND_HUB_OWNER_KEY`, produced once by
GoogleFindMyTools' Chrome login and copied into the secrets provider with
`scripts/seed_find_hub.py`.

Matching is local: a background listener computes the tag's rotating
ephemeral ID (EID) from the key and pair date and hears every advertisement
the tag sends, so every hearing updates the person's presence directly
instead of gambling on a scan window. Find Hub can re-key a tag without any
action on your side, so orc re-reads each tag's key from the account every
four hours; a changed key reaches the listener within five minutes, no
restart.

## Developing

`uv sync --extra test --extra lint` installs `orc` and `orc_extras`
editable, so the dev server runs your working tree and the sample config's
plugin lines resolve against `extras/src` directly. Two entry points in
`src/orc/runner.py` serve on `0.0.0.0:<port>`:

- `orc-dev` runs Flask's dev server, for development.
- `orc` runs gunicorn, for production.

Before your first commit, install the git hooks; ruff, opengrep, mypy, both
test suites, and more run on every commit:

```sh
pre-commit install
```

### Stubs and real backends

Every capability is a `provider` line naming a backend module. The sample
config names the stubs (`orc.dal.<capability>.stub`), so every device and
secret integration is faked in memory. A real installation names the real
backends instead, for example `provider adapter orc.dal.mqtt.hubitat`;
`secrets`, `hubitat`, `adapter`, `broker`, `chromecast`, `audio`, `push`,
and `findhub` default to their real backend when the `provider` line is
omitted, so a production config only needs to name `weather`, `holiday`,
and `blaster`. An explicit `provider` line, stub or real, always overrides
the default.

Three backends are for development against a real house:

- `provider adapter orc.dal.mqtt.none` claims no devices, so every `Light`,
  `Button`, and `Sensor` is virtual and dispatch skips them.
- `provider broker orc.dal.broker.stub` keeps every adapter's routing but
  opens no connection and drops what they publish, so a boot with a plugin
  adapter needs no broker.
- `provider broker orc.dal.broker.readonly` connects and subscribes to the
  real broker named by `mqtt_host` but drops every publish, so you can
  watch the house's devices without being able to command them.

### Layout

- `src/`: the `orc` package and the sample `config.orc` the dev server runs against
- `extras/`: optional `orc_extras` plugin package with its own tests
- `data/`: sibling `orc_data` package (piper voice model and ephemeris)
- `examples/`: copyable per-plugin config samples and an example plugin
- `scripts/`: build and publish scripts
- `tests/`: the core test suite

## Deploy (the author's setup)

This flow targets a private package registry, so if you're installing
elsewhere, use [Install](#install) instead.

`sh scripts/upload.sh` builds and publishes to the internal package
registry. Pass `--full` to also publish the `orc_data` sub-package:

```sh
sh scripts/upload.sh --full
```

The host-side scripts that follow it, one that SSHes to the target host and
one that syncs dependencies, reinstalls from the registry, and bounces the
`orc` supervisor job, are specific to the author's host and aren't in the
repo.

## License and credits

orc is licensed under the GNU General Public License version 3; see
[LICENSE](LICENSE). Parts of orc build on other projects, and those files
carry the notice:

- **GoogleFindMyTools**, Copyright 2024 Leon Böttger, GPL-3.0,
  https://github.com/leonboe1/GoogleFindMyTools. The Find Hub login and the
  Nova device-list request in `src/orc/dal/findhub/nova.py` derive from it,
  and `scripts/seed_find_hub.py` imports the secrets its login writes.
- **GoogleFindMy-HA** by BSkando, GPL-3.0,
  https://github.com/BSkando/GoogleFindMy-HA. The EID variant semantics in
  `src/orc/security.py` and the golden vectors in `tests/test_security.py`
  follow it. The EID scheme itself is Google's public
  [Find Hub Network accessory spec](https://developers.google.com/nearby/fast-pair/specifications/extensions/fmdn).
- **rethink** by anszom, GPL-2.0, https://github.com/anszom/rethink. The
  ThinQ2 "clip" protocol the `lg_ac` plugin speaks (enrollment, the AABB/TLV
  framing, the `lime/devices` transport and the field maps) was
  reverse-engineered there; the plugin is a Python reimplementation.
