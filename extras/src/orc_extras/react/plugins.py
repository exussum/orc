from datetime import datetime
from typing import Any

from orc_engine import model as em

import orc_extras.react
from orc import model as m
from orc.plugins import requires_ctx
from orc.security import safe_eval
from orc_extras.react.model import FUNCTIONS, FormulaSubject, Log, State, Transition, source_of

JOB_ID = "react"


def sleep(ctx: m.AppContext, name: str) -> datetime:
    state = ctx.plugin_state[orc_extras.react]
    until = ctx.api.local_now() + state.groups[name].pause
    state.disabled[name] = until
    for watch in state.watches:
        if watch.rule in state.groups[name].rules:
            ctx.scheduler.cancel(_job(watch))
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


def is_disabled(state: State, watch: em.Watch[Any], now: datetime) -> bool:
    name = state.name_of.get(watch.rule)
    return bool(name and disabled_until(state, name, now))


def _trigger_label(watch: em.Watch[Any]) -> Any:
    match watch.condition:
        case Transition(to=to):
            return to
        case em.Changed(subject=FormulaSubject(expr=expr)):
            return expr
    raise TypeError(f"{watch.condition} has no label")


def _changes(changed: m.MqttDeviceSubject, old: Any, new: Any) -> em.Changes:
    def changes(subject: em.Subject) -> tuple[em.Value, em.Value]:
        if subject == changed:
            return (old, new)
        raise KeyError(subject)

    return changes


def _reader(ctx: m.AppContext) -> em.Read:
    world_read = ctx.api.reader()

    def read(subject: em.Subject) -> em.Value:
        match subject:
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
    awake = [watch for watch in state.watches if not is_disabled(state, watch, now)]
    fired: list[em.Watch[m.Devices]] = []
    for outcome in ctx.engine.evaluate(awake, read=_reader(ctx), changes=_changes(changed, old, new), force=True):
        match outcome:
            case em.Cancel(watch):
                ctx.scheduler.cancel(_job(watch))
            case em.Deferred(watch, when):
                ctx.scheduler.once(_run_react, when, outcome, device.name, name=f"React {device.name}", id=_job(watch))
            case em.Report(em.Watch() as watch, commands) if commands:
                fired.append(watch)
    if fired:
        entries = [_log(ctx, watch, device.name, "") for watch in fired]
        ctx.api.dispatch(ctx.api.squish(map(_command, fired), entries[0]), entry=entries[0])


def _job(watch: em.Watch[Any]) -> str:
    return f"{JOB_ID}-{hash(watch)}"


def _targets(what: m.Devices) -> str:
    return ", ".join(f"`{d.label or d.name}`" for d in what.all())


def _dispatch(ctx: m.AppContext, watch: em.Watch[m.Devices], name: str, note: str) -> None:
    ctx.api.dispatch((_command(watch),), entry=_log(ctx, watch, name, note))


def _log(ctx: m.AppContext, watch: em.Watch[m.Devices], name: str, note: str) -> m.LogEntry:
    command = watch.rule.steps[0].command
    return ctx.api.log(
        Log.REACT,
        f"`{name}` {_trigger_label(watch)}{note} → set {_targets(command.subject)} {command.value}",
        m.Broker(id=str(source_of(watch).value), source="hubitat"),
    )


def _command(watch: em.Watch[m.Devices]) -> m.DeviceCommand:
    command = watch.rule.steps[0].command
    return em.Command(command.subject, command.value, tag=m.Tag.SYSTEM)


@requires_ctx
def _run_react(deferred: em.Deferred[m.Devices], name: str, *, ctx: m.AppContext) -> None:
    watch = deferred.watch
    (report,) = ctx.engine.evaluate((deferred,), read=_reader(ctx), force=True)
    if report.commands:
        _dispatch(ctx, watch, name, f" {int(watch.delay.total_seconds() // 60)}m ago")
