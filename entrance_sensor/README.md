# orc_entrance_sensor

An out-of-tree orc plugin. This README walks through how a plugin is set up,
using this package as the working example. For everything else (running orc,
devices, secrets, environment), see the [main README](../README.md).

## What a plugin is

A plugin is a separate pip-installable package that depends on `orc` and
exposes one or more plain functions. orc discovers them by dotted path from
the `Plugins` table in `config.md` — there is no entry-point registration.

```
entrance_sensor/
├── pyproject.toml                    # package named orc_entrance_sensor, depends on orc
├── src/
│   └── orc_entrance_sensor/
│       ├── __init__.py
│       └── plugins.py                # the plugin functions live here
└── tests/
```

`pyproject.toml` is minimal — the only requirement is a dependency on `orc`:

```toml
[project]
name = "orc_entrance_sensor"
version = "0.0.1"
requires-python = ">=3.11"
dependencies = [
    "orc==0.0.1",
]
```

## 1. Write the plugin function

A plugin function takes a `PluginCtx` as its first argument. The remaining
arguments depend on which section the plugin is registered under (step 2):

| Section   | Called as                      | When                                        |
|-----------|--------------------------------|---------------------------------------------|
| `scene`   | `fn(ctx)`                      | user presses its button on the Scene page   |
| `system`  | `fn(ctx)`                      | user presses its button on the System page  |
| `hubitat` | `fn(ctx, device_id, value)`    | every Hubitat Maker API callback            |

This plugin is a `hubitat` plugin — `trigger_sensor` in
[`src/orc_entrance_sensor/plugins.py`](src/orc_entrance_sensor/plugins.py)
receives every device event and returns early unless `device_id` matches the
sensor it cares about.

### The plugin context

`PluginCtx` (defined in `orc/plugins.py`, built by `build_ctx`) is a small
dataclass that hands a plugin everything it may touch, so plugins never
import orc internals directly:

| Field                  | What it is                    | What it provides                                                                                                              |
|------------------------|-------------------------------|--------------------------------------------------------------------------------------------------------------------------------|
| `ctx.api`              | `orc.api` module          | The verbs of the system. Everything a plugin does to the outside world goes through this module.                                |
| `ctx.model`            | `orc.model` module        | The vocabulary. The data types and constants that the api functions accept and return.                                          |
| `ctx.config`           | runtime configuration     | What the operator decided. The parsed `config.md` tables plus environment settings.                                             |
| `ctx.snapshot_manager` | snapshot manager          | Undo for device state. Records what devices looked like before a plugin changes them, so that state can be restored later.      |
| `ctx.scheduler`        | APScheduler instance      | Deferred work. Lets a plugin queue a follow-up job to run at a later time instead of acting immediately.                        |
| `ctx.orc`              | top-level `orc` package   | The devices themselves. The enums used to say which device a config applies to.                                                 |

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

| Name            | Plugin                                     | Parameters      |
|-----------------|--------------------------------------------|-----------------|
| Entrance Sensor | orc_entrance_sensor.plugins.trigger_sensor | section=hubitat |
```

## 3. Install it

Install the plugin package into the same environment as orc:

```sh
pip install ./entrance_sensor          # alongside: pip install ./data .
```

(For development, `pip install -e ./entrance_sensor`.)

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
        "Day": ("Trigger", "Device", "State"),
        "Night": ("Trigger", "Device", "State"),
    },
)
def trigger_sensor(ctx, sensor, device_id, event):
    ...
```

The schema maps `#####` headings in the markdown file to their table columns:

- **Two-column sections** (`Settings`, `Messages`) flatten into attributes:
  each row's first column becomes the attribute name, the second its value.
  E.g. `sensor.entrance_id`, `sensor.log_shutdown`.
- **Wider sections** (`Day`, `Night`) become a namespace per section, with
  one attribute per group of rows, keyed by the first column. E.g.
  `sensor.day.entrance_light_on` is the list of rows under the
  `entrance_light_on` trigger.

Because this package is outside the `orc` package, the config name is
automatically namespaced as `<package>/<name>`, so the file lives at:

```
$ORC_CONFIG_DIR/plugins/orc_entrance_sensor/entrance_sensor.md
```

The in-repo sample is
[`../src/plugins/orc_entrance_sensor/entrance_sensor.md`](../src/plugins/orc_entrance_sensor/entrance_sensor.md):

```markdown
##### Settings

| Key                   | Value    |
|-----------------------|----------|
| cleanup_delay_minutes | 2        |
| entrance_id           | 1        |
| ...                   | ...      |

##### Day

| Trigger            | Device     | State  |
|--------------------|------------|--------|
| entrance_light_on  | Light      | 20     |
| entrance_light_off | Light      | off    |
| entrance_config    | Light      | on     |
|                    | Chromecast | pause  |
| after_hours        | Chromecast | stop   |
```

The config is loaded lazily on first call and cached; if the file is missing
or malformed, the error is logged and the plugin becomes a no-op instead of
crashing orc.

## Tests

Tests run standalone from this directory — `pyproject.toml` puts the parent
`orc` sources on `pythonpath`, so no install is needed:

```sh
cd entrance_sensor
pytest
```
