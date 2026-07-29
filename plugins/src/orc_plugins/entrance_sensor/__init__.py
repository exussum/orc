"""Entrance-sensor integration: motion-triggered scenes plus battery/door state read
from the central MQTT listener's device documents.

The plugins-module import is deferred into register() for the same reason as the
package-level register(): this package is imported during orc's config load.
"""

from typing import Any


def register(core: Any) -> None:
    from orc_plugins.entrance_sensor import plugins

    core.register_plugin(
        startup=[plugins.start],
        state_providers={"Entrance Sensors": plugins.battery_state},
    )
