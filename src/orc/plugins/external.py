from functools import partial
from typing import Any

from orc import model as m
from orc.locale import Log


def declare(declarations: Any) -> None:
    declarations.declare(setup=[setup])


def setup(ctx: m.AppContext) -> None:
    ctx.api.add_external_listener(partial(_on_external, ctx))


def _on_external(ctx: m.AppContext, device: m.DeviceState, attribute: str, old: Any, new: Any) -> None:
    ctx.api.log(
        m.LogSource.EXTERNAL,
        Log.EXTERNAL_CHANGE.format(device=device.name, attribute=attribute, old=old, new=new),
        m.Broker(id="external", source="hubitat"),
    )
