from functools import partial
from typing import Any

from orc import model as m
from orc.locale import Log


def declare(declarations: Any) -> None:
    declarations.declare(setup=[setup])


def setup(ctx: m.AppContext) -> None:
    ctx.api.add_listener(partial(_on_event, ctx))


def _on_event(ctx: m.AppContext, device: m.DeviceState, attribute: str, old: Any, new: Any) -> None:
    if attribute != "battery":
        return
    level = m.BatteryLevel.from_fraction(new, 100)
    if level.is_critical and not m.BatteryLevel.from_fraction(old, 100).is_critical:
        msg = Log.LOW_BATTERY.format(device=device.name, level=level.value)
        ctx.api.log(
            m.LogSource.SYSTEM, msg, m.Broker(id=str(device.id), source="hubitat"), notification=m.Notification(("battery", device.name))
        )
