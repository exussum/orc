"""Entrance-sensor integration: motion-triggered scenes plus battery/door state read
from the central MQTT listener's device documents.

The plugins-module import is deferred into declare() for the same reason as the
package-level declare(): this package is imported during orc's config load.
"""

import sys
from functools import partial
from typing import Any

_CONFIG = "orc_plugins/entrance_sensor"


def declare(declarations: Any) -> None:
    from orc_plugins.entrance_sensor import plugins

    try:
        sensor = declarations.load_plugin_config(_CONFIG, plugins.SCHEMA)
    except Exception as exc:
        print(f"Failed to load plugin config {_CONFIG!r}: {exc}", file=sys.stderr)
        return
    ids = {sensor.entrance_id, sensor.patio_door_id}
    declarations.declare(setup=[partial(plugins.setup, sensor, ids)])
