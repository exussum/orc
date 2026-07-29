# orc_plugins

An out-of-tree orc plugin package. This README walks through how a plugin is set
up, using the `entrance_sensor` plugin in this package as the working example. For everything else (running orc,
devices, secrets, environment), see the [main README](../README.md).

## What a plugin is

A plugin is a separate pip-installable package that depends on `orc` and
exposes one or more plain functions. orc discovers them by dotted path from
the `Plugins` table in `config.md` — there is no entry-point registration.

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
requires-python = ">=3.11"
dependencies = [
    "orc==0.0.1",
]
```

## 1. Write the plugin function

A plugin function takes a `PluginCtx` as its first argument. The remaining
arguments depend on which section the plugin is registered under (step 2):

| Section  | Called as                | When                                            |
|----------|--------------------------|-------------------------------------------------|
| `scene`  | `fn(ctx)`                | user presses its button on the Scene page       |
| `system` | `fn(ctx)`                | user presses its button on the System page      |
| `device` | `fn(ctx, device=<name>)` | clicked from a device row (`/api/run?device=…`) |

Event-driven plugins don't use the Plugins table at all: the entrance sensor in
[`src/orc_plugins/entrance_sensor/plugins.py`](src/orc_plugins/entrance_sensor/plugins.py)
wires itself in its package ``declare()`` hook — a setup hook receives the
`PluginCtx` and registers an MQTT device listener (`ctx.api.add_listener`) and a
state-page section (`ctx.api.add_state_provider`).

### The plugin context

`PluginCtx` (defined in `orc/plugins.py`, built by `build_ctx`) is a small
dataclass that hands a plugin everything it may touch, so plugins never
import orc internals directly:

| Field                  | What it is              | What it provides                                                                                                            |
|------------------------|-------------------------|-----------------------------------------------------------------------------------------------------------------------------|
| `ctx.api`              | `orc.api` module        | The verbs of the system. Everything a plugin does to the outside world goes through this module.                            |
| `ctx.model`            | `orc.model` module      | The vocabulary. The data types and constants that the api functions accept and return.                                      |
| `ctx.config`           | runtime configuration   | What the operator decided. The parsed `config.md` tables plus environment settings.                                         |
| `ctx.snapshot_manager` | snapshot manager        | Undo for device state. Records what devices looked like before a plugin changes them, so that state can be restored later.  |
| `ctx.scheduler`        | APScheduler instance    | Deferred work. Lets a plugin queue a follow-up job to run at a later time instead of acting immediately.                    |
| `ctx.orc`              | top-level `orc` package | The devices themselves. The enums used to say which device a config applies to.                                             |

For work scheduled to run later (this plugin queues a follow-up job with
`ctx.scheduler.add_job`), decorate the job function with `@requires_ctx` and
rebuild the plugin context inside it with `build_ctx(ctx)` — the scheduler
injects the raw orc context as a `ctx` keyword argument at run time.

## 2. Register it in config.md

Add a row to the `Plugins` table in `$ORC_CONFIG_DIR/config.md`. The
`Plugin` column is the fully qualified dotted path to the function; the
`Parameters` column sets the section (defaults to `scene`):

```markdown
##### Plugins

| Name       | Plugin                           | Parameters              |
|------------|----------------------------------|-------------------------|
| Pair LG TV | orc_plugins.lgtv.plugins.pair_tv | section=device icon=tv  |
```

## 3. Install it

Install the plugin package into the same environment as orc:

```sh
pip install ./plugins          # alongside: pip install ./data .
```

(For development, `pip install -e ./plugins`.)

Restart orc; the function is imported when `config.md` is parsed at startup.
A bad dotted path fails fast with a config error at that point, not at
trigger time.

## Optional: give it a markdown config

Decorating the function with `@plugin_config(name, schema=...)` loads a
per-plugin markdown file and injects the parsed result as the second
argument, before any section-specific arguments:

```python
from orc.plugins import build_ctx, plugin_config, requires_ctx

@plugin_config(
    "entrance_sensor",
    schema={
        "Settings": ("Key", "Value"),
        "Messages": ("Log", "Message"),
        "Rules": ("Trigger", "Device", "State"),
        "Timed": ("Name", "Start", "Stop", "Device", "State"),
    },
)
def start(ctx, sensor):
    ...
```

The schema maps `#####` headings in the markdown file to their table columns:

- **Two-column sections** (`Settings`, `Messages`) flatten into attributes:
  each row's first column becomes the attribute name, the second its value.
  E.g. `sensor.entrance_id`, `sensor.log_shutdown`.
- **Wider sections** (`Rules`, `Timed`) become a namespace per section, with
  one attribute per group of rows, keyed by the first column. E.g.
  `sensor.rules.enter` is the list of rows under the
  `enter` trigger.

This plugin's `Timed` section holds named groups of device rows, one group
per time window. `Start`/`Stop` sit on a group's first row (blank on
continuation rows); the groups are scanned in document order and the first
one whose window contains the current time wins, so an overlapping group
placed higher up overrides the ones below it. The winning group is
dispatched when the sensor goes active, together with the `enter`
rules; the cleanup job dispatches only `Rules` rows (`present`,
`absent`, `shutdown`).

Because this package is outside the `orc` package, the config name is
automatically namespaced as `<package>/<name>`, so the file lives at:

```
$ORC_CONFIG_DIR/plugins/orc_plugins/entrance_sensor.md
```

The in-repo sample is
[`../src/plugins/orc_plugins/entrance_sensor.md`](../src/plugins/orc_plugins/entrance_sensor.md):

```markdown
##### Settings

| Key                   | Value    |
|-----------------------|----------|
| cleanup_delay_minutes | 2        |
| entrance_id           | 1        |
| ...                   | ...      |

##### Rules

| Trigger | Device     | State |
|---------|------------|-------|
| enter   | Light      | on    |
|         | Chromecast | pause |
| inside  | Light      | off   |

##### Timed

| Name  | Start | Stop  | Device | State |
|-------|-------|-------|--------|-------|
| Day   | 8:00  | 22:00 | Light  | 20    |
| Night | 22:00 | 8:00  | Light  | 1     |
```

The config is loaded lazily on first call and cached; if the file is missing
or malformed, the error is logged and the plugin becomes a no-op instead of
crashing orc.

## Tests

Tests run standalone from this directory — `pyproject.toml` puts the parent
`orc` sources on `pythonpath`, so no install is needed:

```sh
cd plugins
pytest
```
