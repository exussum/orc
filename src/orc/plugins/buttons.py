from functools import partial
from typing import Any

from orc import model as m
from orc.locale import Log


def declare(declarations: Any) -> None:
    declarations.declare(setup=[setup])


def setup(ctx: m.AppContext) -> None:
    mapping = {(r.device.value, r.button, r.event): r.action for r in ctx.config.remotes}
    ctx.api.add_listener(partial(_on_button, ctx, mapping))


def _on_button(ctx: m.AppContext, mapping: dict[tuple[Any, int, str], str], status: m.Status) -> None:
    device, attribute, new = status.device, status.attribute, status.new
    action = mapping.get((device.id, new, attribute))
    trigger = m.Button(device.id)
    if action is not None and not ctx.api.run_action(ctx, action, trigger, source=m.LogSource.EXTERNAL):
        msg = Log.BUTTON_ACTION_UNKNOWN.format(id=action)
        entry = ctx.api.log(m.LogSource.SYSTEM, msg, trigger, notification=m.Notification(("button",)))
        ctx.api.alert(m.Alarm.ATTENTION, text=msg, entry=entry)
