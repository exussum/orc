from dataclasses import replace
from functools import partial
from typing import Any

from orc import model as m
from orc.log import Message


def declare(declarations: Any) -> None:
    declarations.declare(setup=[setup])


def setup(ctx: m.AppContext) -> None:
    mapping = {(r.device.value, r.button, r.event): r.action for r in ctx.config.remotes}
    ctx.api.add_listener(partial(_on_event, ctx, mapping))


def _on_event(ctx: m.AppContext, mapping: dict[tuple[Any, int, str], str], status: m.Status) -> None:
    device, attribute, new = status.device, status.attribute, status.new
    action = mapping.get((device.id, new, attribute))
    if action is not None:
        ctx.scheduler.now(_run_button, (action, m.Button(device.id, log_source=m.LogSource.EXTERNAL)), name="Button", queue=m.Queue.ADHOC)


def _run_button(payload: tuple[str, m.Button], *, ctx: m.AppContext) -> None:
    action, trigger = payload
    if not ctx.api.run_action(action, trigger, source=m.LogSource.EXTERNAL):
        msg = Message.BUTTON_ACTION_UNKNOWN.format(id=action)
        entry = ctx.log.write(msg, replace(trigger, log_source=m.LogSource.SYSTEM), notification=m.Notification(("button",)))
        ctx.api.alert(m.Alarm.ATTENTION, text=msg, entry=entry)
