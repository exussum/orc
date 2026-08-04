import sys
from functools import partial
from typing import TYPE_CHECKING, Any

from orc_plugins.entrance_sensor import plugins

from orc.declarations import load_plugin_config

if TYPE_CHECKING:
    from orc.model import AppContext

CONFIG = "orc_plugins/entrance_sensor"


def declare(declarations: Any) -> None:
    declarations.declare(setup=[setup])


def setup(ctx: "AppContext") -> None:
    try:
        sensor = load_plugin_config(
            CONFIG,
            ctx.config.plugin_docs,
            {
                "Settings": ("Key", "Value"),
                "Messages": ("Log", "Message"),
                "Rules": ("Trigger", "Device", "State"),
                "Timed": ("Name", "Start", "Stop", "Device", "State"),
            },
        )
    except Exception as exc:
        print(f"Failed to load plugin config {CONFIG!r}: {exc}", file=sys.stderr)
        return
    ids = {sensor.entrance_id, sensor.patio_door_id}
    ctx.api.add_listener(partial(plugins._on_sensor_event, ctx, sensor, ids))
    ctx.api.add_state_provider("Entrance Sensors", partial(plugins.battery_state, ctx, ids))
