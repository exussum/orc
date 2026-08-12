# orc_plugins

An out-of-tree orc plugin package. This README walks through how a plugin is set
up, using the `entrance_sensor` plugin in this package as the working example. For everything else (running orc,
devices, secrets, environment), see the [main README](../README.md).

## What a plugin is

A plugin is a separate pip-installable package that depends on `orc` and
exposes one or more plain functions. orc discovers them by dotted path from
`plugin` lines in `config.orc` — there is no entry-point registration.

```
plugins/
├── pyproject.toml                    # package named orc_plugins, depends on orc
├── src/
│   └── orc_plugins/
│       └── entrance_sensor/
│           ├── __init__.py
│           └── plugins.py            # the plugin functions live here
└── tests/
```

`pyproject.toml` is minimal — the only requirement is a dependency on `orc`:

```toml
[project]
name = "orc_plugins"
version = "0.0.1"
requires-python = ">=3.14"
dependencies = [
    "orc==0.0.1",
]
```

## 1. Write the plugin function

A plugin function takes an `AppContext` as its first argument. The remaining
arguments depend on which section the plugin is registered under (step 2):

| Section  | Called as                | When                                                      |
|----------|--------------------------|-----------------------------------------------------------|
| `scene`  | `fn(ctx)`                | user presses its button on the Scene page                 |
| `system` | `fn(ctx)`                | user presses its button on the System page                |
| `device` | `fn(ctx, device=<name>)` | clicked from a device row (`/api/run/<id>?device=<name>`) |

Event-driven plugins don't need a `plugin` line of their own, but those
lines still drive discovery: a package is only imported because some
function in it appears there, and only then do its `declare()` hooks run. A
package containing nothing but event-driven plugins needs at least one
`plugin` line to be imported at all. The entrance sensor in
[`src/orc_plugins/entrance_sensor/plugins.py`](src/orc_plugins/entrance_sensor/plugins.py)
wires itself in its package ``declare()`` hook — a setup hook receives the
context and registers an MQTT device listener (`ctx.api.add_listener`) and a
state-page section (`ctx.api.add_state_provider`).

### The context

`AppContext` (defined in `orc/model.py`) is a small dataclass that hands a
plugin everything it may touch, so plugins never import orc internals
directly:

| Field                  | What it is              | What it provides                                                                                                           |
|------------------------|-------------------------|----------------------------------------------------------------------------------------------------------------------------|
| `ctx.api`              | `orc.api` module        | The verbs of the system. Everything a plugin does to the outside world goes through this module.                           |
| `ctx.model`            | `orc.model` module      | The vocabulary. The data types and constants that the api functions accept and return.                                     |
| `ctx.config`           | runtime configuration   | What the operator decided. The parsed `config.orc` plus environment settings.                                               |
| `ctx.snapshot_manager` | snapshot manager        | Undo for device state. Records what devices looked like before a plugin changes them, so that state can be restored later. |
| `ctx.scheduler`        | APScheduler instance    | Deferred work. Lets a plugin queue a follow-up job to run at a later time instead of acting immediately.                   |
| `ctx.orc`              | top-level `orc` package | The devices themselves. The enums used to say which device a config applies to.                                            |

For work scheduled to run later (this plugin queues a follow-up job with
`ctx.scheduler.add_job`), decorate the job function with `@requires_ctx` —
the scheduler injects the context as a `ctx` keyword argument at run time.

## 2. Register it in config.orc

Add a `plugin` line to `$ORC_CONFIG_DIR/config.orc`: a display name, the
fully qualified dotted path of the function, and optional flags for the
section (defaults to `scene`), icon, and delay:

```
plugin 'Pair LG TV' orc_plugins.lgtv.plugins.pair_tv --section device --icon tv
```

## 3. Install it

Install the plugin package into the same environment as orc:

```sh
pip install ./plugins          # alongside: pip install ./data .
```

(For development, `pip install -e ./plugins`.)

Restart orc; the function is imported when `config.orc` is parsed at startup.
A bad dotted path fails fast with a config error at that point, not at
trigger time.

## Optional: give it a config file

Call `load_plugin_config` from the package's setup hook with a grammar
describing the file's commands, which commands are scalar vs grouped, and a
factory per declared command:

```python
from typing import Any, NamedTuple

from orc.loader import load_plugin_config

CONFIG = "orc_plugins/entrance_sensor"
GRAMMAR = """
setting <key> <value>
message <log> <message>
rules <trigger> <device> <state>
timed define <name> <start> <stop>
timed append <name> <device> <state>
"""


class Settings(NamedTuple):
    entrance_id: int
    snapshot: int


class Rule(NamedTuple):
    device: Any
    state: Any


def setup(ctx):
    sensor = load_plugin_config(
        CONFIG,
        ctx.config.plugin_configs,
        GRAMMAR,
        serializers={"setting": Settings, "message": Messages, "rules": Rule, "timed": Timed},
        scalars=("setting", "message"),
        grouped=("rules", "timed"),
    )
```

The grammar is one docopt pattern per line; the first word is the command.
Values are cast by placeholder name: `<value>` becomes an int when numeric,
`<start>`/`<stop>` become times, `<device>` resolves against the device
enums, `<state>` is validated. Every command is declared in `scalars` or
`grouped`, and every object in the result is built by a factory the plugin
supplies:

- **`scalars` commands** (`setting`, `message`) take exactly two
  placeholders. Their key/value pairs accumulate across the file (a repeated
  key is an error) and the factory is called once at the end with them as
  keyword arguments: `sensor.setting.entrance_id`. A `NamedTuple` factory
  makes every field required, so a missing setting fails at load.
- **`grouped` commands** (`rules`, `timed`) call their factory once per
  line with the line's fields as keyword arguments; rows collect in dicts of
  lists keyed by the first placeholder: `sensor.rules["enter"]`. A grouped
  command with `define`/`append` patterns hoists shared values: `define`
  names a group and carries its parameters (here the time window), `append`
  adds a row, and every row carries the group's parameters merged in. This
  plugin narrows the open-keyed dict right after loading —
  `Rules(**sensor.rules)` — so a missing or misspelled trigger also fails at
  load.

This plugin's `timed` groups hold device rows, one group per time window;
the groups are scanned in file order and the first one whose window contains
the current time wins, so an overlapping group placed higher up overrides
the ones below it. The winning group is dispatched when the sensor goes
active, together with the `enter` rules; the cleanup job dispatches only
`rules` rows (`present`, `absent`, `shutdown`).

Because this package is outside the `orc` package, the config name is
namespaced as `<package>/<name>`, so the file lives at:

```
$ORC_CONFIG_DIR/plugins/orc_plugins/entrance_sensor.orc
```

The in-repo sample is
[`../src/plugins/orc_plugins/entrance_sensor.orc`](../src/plugins/orc_plugins/entrance_sensor.orc):

```
setting entrance_id 1
setting snapshot    45

rules enter  Light      on
rules enter  Chromecast pause
rules inside Light      off

timed define Day 8:00  22:00
timed append Day Light 20
```

The config loads once at startup in `setup()`; if the file is missing or
malformed, the error is logged and the plugin becomes a no-op instead of
crashing orc.

## Tests

Tests run standalone from this directory — `pyproject.toml` puts the parent
`orc` sources on `pythonpath`, so no install is needed:

```sh
cd plugins
pytest
```
