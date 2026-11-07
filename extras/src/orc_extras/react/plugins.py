from datetime import datetime
from typing import Any

from orc_engine import model as em

import orc_extras.react
from orc import model as m
from orc_extras.react.model import AcSubject, CastSubject, Device, FormulaSubject, Log, MqttDeviceSubject, State, Transition

JOB_ID = "react"


def sleep(ctx: m.AppContext, name: str) -> datetime:
    state = ctx.plugin_state[orc_extras.react]
    until = ctx.api.local_now() + state.groups[name].pause
    state.disabled[name] = until
    for watch in state.watches:
        if watch in state.groups[name].watches:
            ctx.scheduler.cancel(_job(watch), m.Queue.ADHOC)
    ctx.log.write(Log.REACT, f"`{name}` sleeping until {until:%H:%M}", m.Manual("react"))
    return until


def wake(ctx: m.AppContext, name: str) -> None:
    ctx.plugin_state[orc_extras.react].disabled.pop(name, None)
    ctx.log.write(Log.REACT, f"`{name}` awake", m.Manual("react"))


def disabled_until(state: State, name: str, now: datetime) -> datetime | None:
    until = state.disabled.get(name)
    if until and until > now:
        return until
    state.disabled.pop(name, None)
    return None


def is_disabled(state: State, watch: em.Watch[Any], now: datetime) -> bool:
    name = state.name_of.get(watch)
    return bool(name and disabled_until(state, name, now))


def _trigger_label(watch: em.Watch[Any], state: State) -> Any:
    if isinstance(watch.condition, Transition):
        return watch.condition.to
    return state.name_of.get(watch, "")


def _changes(changed: MqttDeviceSubject, old: Any, new: Any) -> em.Changes:
    def changes(subject: em.Subject) -> tuple[em.Value, em.Value]:
        if subject == changed or subject == Device(changed.device):
            return (old, new)
        raise KeyError(subject)

    return changes


def _reader(ctx: m.AppContext) -> em.Read:
    world_read = ctx.api.reader()

    def read(subject: em.Subject) -> em.Value:
        match subject:
            case MqttDeviceSubject(device, attribute):
                found = ctx.api.device_state(str(device.value))
                return found.attributes.get(attribute) if found else None
            case AcSubject(device):
                status = next((s for s in ctx.api.capture_acs() if s.what is device), None)
                return status.state if status else None
            case CastSubject(device):
                sound = next((s for s in ctx.api.capture_sounds() if s.what is device), None)
                return sound.playback.value if sound else None
            case FormulaSubject(function, args):
                return function(ctx, *args)
            case _:
                return world_read(subject)

    return read


def _on_event(ctx: m.AppContext, sources: dict[str, m.DeviceEnum], status: m.Status) -> None:
    device, attribute, old, new = status.device, status.attribute, status.old, status.new
    source = sources.get(device.id)
    if source is None:
        return
    changed = MqttDeviceSubject(source, attribute)
    state = ctx.plugin_state[orc_extras.react]
    now = ctx.api.local_now()
    awake = [watch for watch in state.watches if not is_disabled(state, watch, now)]
    fired: list[em.Watch[m.Devices]] = []
    for outcome in ctx.engine.evaluate(awake, read=_reader(ctx), changes=_changes(changed, old, new), force=True):
        match outcome:
            case em.Cancel(watch):
                ctx.scheduler.cancel(_job(watch), m.Queue.ADHOC)
            case em.Deferred(watch, when):
                ctx.scheduler.once(_run_react, when, (outcome, device), id=_job(watch), name=watch.rule.name)
            case em.Report(em.Watch() as watch, commands) if commands:
                fired.append(watch)
    if fired:
        entry = _log(ctx, fired[0], device)
        for watch in fired[1:]:
            entry.add(Log.REACT, _line(ctx, watch, device))
        ctx.api.dispatch(ctx.api.squish(map(_command, fired), entry), entry=entry)


def _job(watch: em.Watch[Any]) -> str:
    return f"{JOB_ID}-{hash(watch)}"


def _targets(what: m.Devices) -> str:
    return ", ".join(f"`{d.label or d.name}`" for d in what.all())


def _dispatch(ctx: m.AppContext, watch: em.Watch[m.Devices], device: m.Device) -> None:
    ctx.api.dispatch((_command(watch),), entry=_log(ctx, watch, device))


def _log(ctx: m.AppContext, watch: em.Watch[m.Devices], device: m.Device) -> m.LogEntry:
    return ctx.log.write(Log.REACT, _line(ctx, watch, device), m.Broker(id=device.id, source=device.source))


def _line(ctx: m.AppContext, watch: em.Watch[m.Devices], device: m.Device) -> str:
    state = ctx.plugin_state[orc_extras.react]
    command = watch.rule.steps[0].command
    return f"`{device.name}` {_trigger_label(watch, state)} → set {_targets(command.subject)} {command.value}"


def _command(watch: em.Watch[m.Devices]) -> m.DeviceCommand:
    command = watch.rule.steps[0].command
    return em.Command(command.subject, command.value, tag=em.Always())


def _run_react(payload: tuple[em.Deferred[m.Devices], m.Device], *, ctx: m.AppContext) -> None:
    deferred, device = payload
    watch = deferred.watch
    (report,) = ctx.engine.evaluate((deferred,), read=_reader(ctx), force=True)
    if report.commands:
        _dispatch(ctx, watch, device)
