"""Entrance-sensor integration: motion-triggered scenes plus a nightly battery poll
of its Hubitat sensors.

The plugins-module import is deferred into register() for the same reason as the
package-level register(): this package is imported during orc's config load.
"""

from typing import Any


def register(core: Any) -> None:
    from orc_plugins.entrance_sensor import plugins

    from orc.model import CronJob

    core.register_plugin(
        crons={"entrance-battery-cron": CronJob(plugins._run_poll_battery, "30 3 * * *", "Entrance Battery Cron")},
        state_providers={"Entrance Sensors": plugins.battery_state},
    )
