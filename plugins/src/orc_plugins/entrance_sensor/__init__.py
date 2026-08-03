"""Entrance-sensor integration: motion-triggered scenes plus battery/door state read
from the central MQTT listener's device documents.

The plugins-module import is deferred into setup() for the same reason as the
package-level declare(): this package is imported during orc's config load.
"""

import sys
from functools import partial
from typing import TYPE_CHECKING, Any

from orc.declarations import load_plugin_config

if TYPE_CHECKING:
    from orc.model import AppContext

CONFIG = "orc_plugins/entrance_sensor"


def declare(declarations: Any) -> None:
    declarations.declare(setup=[setup])


def setup(ctx: "AppContext") -> None:
    from orc_plugins.entrance_sensor import plugins

    try:
        sensor = load_plugin_config(CONFIG, ctx.config.plugin_docs, plugins.SCHEMA)
    except Exception as exc:
        print(f"Failed to load plugin config {CONFIG!r}: {exc}", file=sys.stderr)
        return
    ids = {sensor.entrance_id, sensor.patio_door_id}
    ctx.api.add_listener(partial(plugins._on_sensor_event, ctx, sensor, ids))
    ctx.api.add_state_provider("Entrance Sensors", partial(plugins.battery_state, ctx, ids))
