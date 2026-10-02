from datetime import datetime
from typing import Any

import orc_extras.react
from orc import model as m
from orc.kernel import cast, engine
from orc.plugins import requires_ctx
from orc.security import safe_eval
from orc_extras.react.model import FUNCTIONS, ChangeSubject, DeviceChanged, FormulaSubject, Log, State, Transition, source_of

JOB_ID = "react"


def sleep(ctx: m.AppContext, name: str) -> datetime:
    state = ctx.plugin_state[orc_extras.react]
    until = ctx.api.local_now() + state.groups[name].pause
    state.disabled[name] = until
    for automation in state.automations:
        if automation.rule in state.groups[name].rules:
            ctx.scheduler.cancel(_job(automation))
    ctx.api.log(Log.REACT, f"`{name}` sleeping until {until:%H:%M}", m.Manual("react"))
    return until


def wake(ctx: m.AppContext, name: str) -> None:
    ctx.plugin_state[orc_extras.react].disabled.pop(name, None)
    ctx.api.log(Log.REACT, f"`{name}` awake", m.Manual("react"))


def disabled_until(state: State, name: str, now: datetime) -> datetime | None:
    until = state.disabled.get(name)
    if until and until > now:
        return until
    state.disabled.pop(name, None)
    return None


def is_disabled(state: State, automation: engine.Automation[Any], now: datetime) -> bool:
    name = state.name_of.get(automation.rule)
    return bool(name and disabled_until(state, name, now))


def _trigger_label(automation: engine.Automation[Any]) -> Any:
    trigger = automation.trigger
    if isinstance(trigger, Transition):
        return trigger.to
    return cast.instance(trigger, DeviceChanged).expr


def _reader(ctx: m.AppContext, changed: m.MqttDeviceSubject | None = None, old: Any = None, new: Any = None) -> engine.Read:
    world_read = ctx.api.world_reader()

    def read(subject: engine.Subject) -> engine.Value:
        match subject:
            case ChangeSubject(m.Devices() as devices) if changed is not None:
                return (old, new) if changed.device in devices.all() else None
            case ChangeSubject(watched):
                return (old, new) if changed == watched else None
            case m.MqttDeviceSubject(device, attribute):
                found = next((s for s in ctx.api.device_states() if s.id == device.value), None)
                return found.attributes.get(attribute) if found else None
            case m.AcSubject(device):
                status = next((s for s in ctx.api.capture_acs() if s.what is device), None)
                return status.state if status else None
            case m.CastSubject(device):
                sound = next((s for s in ctx.api.capture_sounds() if s.what is device), None)
                return sound.playback if sound else None
            case FormulaSubject(device, expr):
                target = str(device.value)
                found = ctx.api.device_state(target)
                if found is None:
                    raise KeyError(target)
                ns: dict[str, Any] = {**FUNCTIONS, **{name: _num(value) for name, value in found.attributes.items()}}
                try:
                    return safe_eval(expr, ns)
                except Exception as exc:
                    raise ValueError(f"react rule `{expr}` on `{device.name}`: {exc}") from exc
            case _:
                return world_read(subject)

    return read


def _num(value: Any) -> Any:
    try:
        return float(value)
    except TypeError, ValueError:
        return value


def _on_event(ctx: m.AppContext, sources: dict[int, m.DeviceEnum], device: m.DeviceState, attribute: str, old: Any, new: Any) -> None:
    source = sources.get(device.id)
    if source is None:
        return
    changed = m.MqttDeviceSubject(source, attribute)
    state = ctx.plugin_state[orc_extras.react]
    now = ctx.api.local_now()
    read = _reader(ctx, changed, old, new)
    awake = [automation for automation in state.automations if not is_disabled(state, automation, now)]
    fired: list[engine.Automation[m.Devices]] = []
    for outcome in ctx.engine.evaluate(awake, read=read, force=True):
        match outcome:
            case engine.Cancel(automation):
                ctx.scheduler.cancel(_job(automation))
            case engine.Deferred(automation, when):
                ctx.scheduler.once(_run_react, when, outcome, device.name, name=f"React {device.name}", id=_job(automation))
            case engine.Report(engine.Automation() as automation, commands) if commands:
                fired.append(automation)
    if fired:
        entries = [_log(ctx, automation, device.name, "") for automation in fired]
        ctx.api.dispatch(ctx.api.squish(map(_command, fired), entries[0]), entry=entries[0])


def _job(automation: engine.Automation[Any]) -> str:
    return f"{JOB_ID}-{hash(automation)}"


def _targets(what: m.Devices) -> str:
    return ", ".join(f"`{d.label or d.name}`" for d in what.all())


def _dispatch(ctx: m.AppContext, automation: engine.Automation[m.Devices], name: str, note: str) -> None:
    ctx.api.dispatch((_command(automation),), entry=_log(ctx, automation, name, note))


def _log(ctx: m.AppContext, automation: engine.Automation[m.Devices], name: str, note: str) -> m.LogEntry:
    command = automation.rule.steps[0].command
    return ctx.api.log(
        Log.REACT,
        f"`{name}` {_trigger_label(automation)}{note} → set {_targets(command.subject)} {command.value}",
        m.Broker(id=str(source_of(automation).value), source="hubitat"),
    )


def _command(automation: engine.Automation[m.Devices]) -> m.DeviceCommand:
    command = automation.rule.steps[0].command
    return engine.Command(command.subject, command.value, tag=m.Tag.SYSTEM)


@requires_ctx
def _run_react(deferred: engine.Deferred[m.Devices], name: str, *, ctx: m.AppContext) -> None:
    automation = deferred.automation
    (report,) = ctx.engine.evaluate((deferred,), read=_reader(ctx), force=True)
    if report.commands:
        _dispatch(ctx, automation, name, f" {int(automation.delay.total_seconds() // 60)}m ago")
