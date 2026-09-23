# orc_extras

An out-of-tree orc plugin package. This README walks through how a plugin is set
up, using the `entrance_sensor` plugin in this package as the working example. For everything else (running orc,
devices, secrets, environment), see the [main README](../README.md).

## What a plugin is

A plugin is a separate pip-installable package that depends on `orc` and
exposes one or more plain functions. orc discovers them by dotted path from
`plugin` lines in `config.orc` — there is no entry-point registration.

```
extras/
├── pyproject.toml                    # package named orc_extras, depends on orc
├── src/
│   └── orc_extras/
│       └── entrance_sensor/
│           ├── __init__.py
│           └── plugins.py            # the plugin functions live here
└── tests/
```

`pyproject.toml` is minimal — the only requirement is a dependency on `orc`:

```toml
[project]
name = "orc_extras"
version = "0.0.1"
requires-python = ">=3.14"
dependencies = [
    "orc==0.0.1",
]
```

## 1. Write the plugin function

A plugin function is called as `fn(ctx, device)`: the `AppContext` and the
device name from the invocation, `None` when none was passed. Which section
the plugin is registered under (step 2) decides where its button appears:

| Section  | Called as         | When                                                      |
| -------- | ----------------- | --------------------------------------------------------- |
| `scene`  | `fn(ctx, None)`   | user presses its button on the Scene page                 |
| `system` | `fn(ctx, None)`   | user presses its button on the System page                |
| `device` | `fn(ctx, <name>)` | clicked from a device row (`/api/run/<id>?device=<name>`) |

Event-driven plugins don't need a `<function>` of their own, but `plugin`
lines still drive discovery: a package is only imported because a `plugin`
line names it, and only then does its `declare()` hook run. A package
containing nothing but event-driven plugins gets a bare line:

```
plugin 'Entrance Sensor' orc_extras.entrance_sensor
```

The entrance sensor in
[`src/orc_extras/entrance_sensor/plugins.py`](src/orc_extras/entrance_sensor/plugins.py)
wires itself in its package's `setup()` hook, registered by `declare()` — it
receives the context and registers an MQTT device listener
(`ctx.api.add_listener`) and a state-page section
(`ctx.api.add_state_provider`).

### The context

`AppContext` (defined in `orc/model.py`) is a small dataclass that hands a
plugin everything it may touch, so plugins never import orc internals
directly:

| Field                  | What it is              | What it provides                                                                                                           |
| ---------------------- | ----------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `ctx.api`              | `orc.api` module        | The verbs of the system. Everything a plugin does to the outside world goes through this module.                           |
| `ctx.model`            | `orc.model` module      | The vocabulary. The data types and constants that the api functions accept and return.                                     |
| `ctx.config`           | runtime configuration   | What the operator decided. The parsed `config.orc` plus environment settings.                                              |
| `ctx.snapshot_manager` | snapshot manager        | Undo for device state. Records what devices looked like before a plugin changes them, so that state can be restored later. |
| `ctx.scheduler`        | APScheduler instance    | Deferred work. Lets a plugin queue a follow-up job to run at a later time instead of acting immediately.                   |
| `ctx.orc`              | top-level `orc` package | The devices themselves. The enums used to say which device a config applies to.                                            |
| `ctx.plugin_state`     | per-plugin state dict   | Shared live state, keyed by the plugin's module and stored once by `setup()`. Wiring travels as arguments instead.         |

For work scheduled to run later (this plugin queues a follow-up job with
`ctx.scheduler.add_job`), decorate the job function with `@requires_ctx` —
the scheduler injects the context as a `ctx` keyword argument at run time.

## 2. Register it in config.orc

Add a `plugin` line to `$ORC_CONFIG_DIR/config.orc`: a display name, the
plugin's package, an optional function name (imported from the package
itself, so it must be re-exported there — for example
`from orc_extras.lg_tv.plugins import pair_tv` makes the bare name `pair_tv`
resolve), section (defaults to `scene`), icon, and delay:

```
plugin 'Pair LG TV' orc_extras.lg_tv pair_tv --section device --icon tv
```

## 3. Install it

Install the plugin package into the same environment as orc:

```sh
pip install ./extras            # alongside: pip install ./data .
```

(For development, `pip install -e ./extras`.)

Restart orc; the function is imported when `config.orc` is parsed at startup.
A bad dotted path fails fast with a config error at that point, not at
trigger time.

## Optional: give it a config file

Call `load_plugin_config` from the package's setup hook with a grammar
describing the file's commands and a `command_cfg` serializer — `scalar()`,
`group()`, or `array()` — wrapping a factory per declared command:

```python
from functools import partial
from typing import Any, NamedTuple

from command_cfg import group, scalar
from orc.kernel.loader import Cast, load_plugin_config

CONFIG = "orc_extras/entrance_sensor"
GRAMMAR = """
setting <key> <value>
message <log> <message>
rules <trigger> <routine>
timed <name> <start> <stop> <routine>
"""


class Settings(NamedTuple):
    entrance_id: int
    snapshot: int


def _rule(ctx, **values: Any) -> Any:
    return _routine_commands(ctx, values["routine"])


def setup(ctx):
    sensor = load_plugin_config(
        CONFIG,
        ctx.config,
        GRAMMAR,
        serializers={
            "setting": scalar(Settings, types={"entrance_id": int, "snapshot": int}),
            "message": scalar(Messages),
            "rules": group(partial(_rule, ctx)),
            "timed": group(partial(_timed, ctx)),
        },
    )
```

(Abbreviated: `Messages`, `Timed`, `_timed`, and `_routine_commands` are
omitted, and `Settings` is shown with fewer fields than the real one — see
[`src/orc_extras/entrance_sensor/__init__.py`](src/orc_extras/entrance_sensor/__init__.py)
for the full plugin.)

The grammar is one docopt pattern per line; the first word is the command.
Values arrive as strings; a serializer's `types=` mapping (field name to
callable) coerces the ones that need it — here `entrance_id`/`snapshot`
become `int`. Anything not listed in `types=` stays a string, so a factory
that needs a non-primitive value (`<routine>` resolved to commands against
the main config's ad_hoc routines and routine ids, `<start>`/`<stop>` parsed
into times) does that conversion itself, as `_rule`/`_timed` do above:

- **`scalar(...)` commands** (`setting`, `message`) take exactly two
  placeholders. Their key/value pairs accumulate across the file (a repeated
  key is an error) and the factory is called once at the end with them as
  keyword arguments: `sensor.setting.entrance_id`. A `NamedTuple` factory
  makes every field required, so a missing setting fails at load.
- **`group(...)` commands** (`rules`, `timed`) call their factory once per
  line with the line's fields as keyword arguments; rows collect in dicts of
  lists keyed by the first placeholder: `sensor.rules["present"]`. This
  plugin narrows the open-keyed dict right after loading —
  `Rules(**sensor.rules)` — so a missing or misspelled trigger also fails at
  load.

Each `rules` and `timed` line names a routine from the main `config.orc` —
an ad_hoc by its name or a routine by its id. The plugin resolves the name
to the routine's commands at load and dispatches them directly: an ad_hoc's
reset base is composed in exactly as running it from the UI would, but its
`--delay` and `--snapshot` are ignored. The `timed` windows are scanned in
file order and the first one containing the current time wins, so an
overlapping window placed higher up overrides the ones below it. The winning
window's routine is dispatched when the sensor goes active; reactions to
arrival itself (e.g. pausing media, turning on a light) belong in a `react`
rule on the same sensor instead. The cleanup job dispatches only `rules`
routines (`present`, `absent`, `shutdown`).

If the main config defines BLE `tag` lines, `cleanup_delay_minutes` must be
at least 1. The cleanup pauses presence at the door event and only counts
evidence heard afterwards, and a staying tag's advertisements can drop out
for most of a minute, so a faster cleanup could read someone still home as
absent; `setup()` rejects the config at startup.

Because this package is outside the `orc` package, the config name is
namespaced as `<package>/<name>`, so the file lives at:

```
$ORC_CONFIG_DIR/plugins/orc_extras/entrance_sensor.orc
```

For example:

```
setting cleanup_delay_minutes 2
setting entrance_id           1
setting patio_door_id         56
setting active_event          active
setting inactive_event        inactive
setting snapshot              45
setting listener              Rex

message log_present   'Trigger sensor off: skip (people present)'
message log_door_open 'Trigger sensor off: skip (patio door open)'
message log_absent    'Trigger sensor off: skip (listener home)'
message log_shutdown  'Trigger sensor off: applying OFF'

rules inside   'All Lights Off'
rules present  Silence
rules absent   Dog
rules shutdown 'All Lights Off'

timed Day   8:00  22:00 'All Lights On'
timed Night 22:00 8:00  Silence
```

The config loads once at startup in `setup()`; if the file is missing or
malformed, the error is logged and the plugin becomes a no-op instead of
crashing orc.

## Tests

Tests run standalone from this directory (`extras/`) — `pyproject.toml` puts
the parent `orc` sources on `pythonpath`, so no install is needed:

```sh
cd extras
pytest
```
