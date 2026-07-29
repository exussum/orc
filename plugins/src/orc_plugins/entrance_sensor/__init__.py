"""Entrance-sensor integration: motion-triggered scenes plus battery/door state read
from the central MQTT listener's device documents.

The plugins-module import is deferred into declare() for the same reason as the
package-level declare(): this package is imported during orc's config load.
"""

from typing import Any


def declare(declarations: Any) -> None:
    from orc_plugins.entrance_sensor import plugins

    declarations.declare(setup=[plugins.start])
