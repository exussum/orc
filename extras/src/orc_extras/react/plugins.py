from dataclasses import replace
from datetime import datetime
from typing import Any

from orc_engine import model as em

import orc_extras.react
from orc import model as m
from orc.collections import partition
from orc_extras.react.model import AcSubject, CastSubject, Device, FormulaSubject, Log, MqttDeviceSubject, State, Transition

JOB_ID = "react"


def sleep(ctx: m.AppContext, name: str) -> datetime:
    state = ctx.plugin_state[orc_extras.react]
    until = ctx.api.local_now() + state.groups[name].pause
    state.disabled[name] = until
    for watch in state.watches:
        if watch in state.groups[name].watches:
            ctx.scheduler.cancel(_job(watch), m.Queue.ADHOC)
            state.delayed.pop(watch, None)
    ctx.log.write(f"`{name}` sleeping until {until:%H:%M}", trigger)
    return until


def wake(ctx: m.AppContext, name: str) -> None:
    ctx.plugin_state[orc_extras.react].disabled.pop(name, None)
    ctx.log.write(f"`{name}` awake", trigger)


def disabled_until(state: State, name: str, now: datetime) -> datetime | None:
    until = state.disabled.get(name)
    if until and until > now:
        return until
    state.disabled.pop(name, None)
    return None


def delayed_until(state: State, name: str, now: datetime) -> datetime | None:
    ends = [state.delayed[w] for w in state.groups[name].watches if w in state.delayed]
    until = max(ends, default=None)
    return until if until and until > now else None


def cooling_until(state: State, name: str, now: datetime) -> datetime | None:
    ends = [state.fired[w] + max(w.cooldown, w.delay) for w in state.groups[name].watches if w in state.fired]
    until = max(ends, default=None)
    return until if until and until > now else None


def is_disabled(state: State, watch: em.Watch[Any], now: datetime) -> bool:
    name = state.name_of.get(watch)
    return bool(name and disabled_until(state, name, now))


def _trigger_label(watch: em.Watch[Any], state: State) -> Any:
    if isinstance(watch.condition, Transition):
        return watch.condition.to
    return state.name_of.get(watch, "")


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
                sound = next((s for s in ctx.api.sound_states() if s.what is device), None)
                return sound.playback.value if sound else None
            case FormulaSubject(function, args):
                return function(ctx, *args)
            case _:
                return world_read(subject)

    return read


def _on_event(ctx: m.AppContext, sources: dict[str, m.DeviceEnum], status: m.Status) -> None:
    if status.device.id in sources:
        ctx.scheduler.now(_run_event, (sources, status), name="React")


def _run_event(payload: tuple[dict[str, m.DeviceEnum], m.Status], *, ctx: m.AppContext) -> None:
    sources, status = payload
    _react(ctx, sources, status, ctx.plugin_state[orc_extras.react].watches)


def _react(ctx: m.AppContext, sources: dict[str, m.DeviceEnum], status: m.Status, watches: list[em.Watch[m.Devices]]) -> None:
    device, attribute, old, new = status.device, status.attribute, status.old, status.new
    source = sources.get(device.id)
    if source is None:
        return
    changes = (em.Change(MqttDeviceSubject(source, attribute), old, new), em.Change(Device(source), old, new))
    state = ctx.plugin_state[orc_extras.react]
    now = ctx.api.local_now()

    awake, paused = partition(watches, lambda e: is_disabled(state, e, now))

    fired: list[em.Watch[m.Devices]] = []
    for outcome in ctx.engine.evaluate(awake, read=_reader(ctx), changes=changes, force=True):
        match outcome:
            case em.Cancel(watch):
                state.delayed.pop(watch, None)
                ctx.scheduler.cancel(_job(watch), m.Queue.ADHOC)
            case em.Defer(watch, when):
                state.fired[watch] = now
                state.delayed[watch] = when
                ctx.scheduler.once(_run_react, when, (outcome, device), id=_job(watch), name=state.name_of[watch])
            case em.Report(em.Watch() as watch, commands) if commands:
                state.fired[watch] = now
                fired.append(watch)

    for watch in paused:
        name = state.name_of[watch]
        ctx.scheduler.once(_recheck, state.disabled[name], (ctx, sources, status, watch), id=f"{JOB_ID}-recheck-{name}", name=name)

    if fired:
        react = _log(ctx, fired[0], device, trigger)
        for watch in fired[1:]:
            ctx.log.write(_line(ctx, watch, device), react)
        ctx.api.dispatch(ctx.api.squish(map(_command, fired), react), trigger=react)


def _recheck(ctx: m.AppContext, sources: dict[str, m.DeviceEnum], status: m.Status, watch: em.Watch[m.Devices]) -> None:
    current = _reader(ctx)(MqttDeviceSubject(sources[status.device.id], status.attribute))
    _react(ctx, sources, replace(status, new=current), [watch])


def _job(watch: em.Watch[Any]) -> str:
    return f"{JOB_ID}-{hash(watch)}"


def _targets(what: m.Devices) -> str:
    return ", ".join(f"`{d.label or d.name}`" for d in what.all())


def _dispatch(ctx: m.AppContext, watch: em.Watch[m.Devices], device: m.Device, trigger: m.Trigger) -> None:
    ctx.api.dispatch((_command(watch),), trigger=_log(ctx, watch, device, trigger))


def _log(ctx: m.AppContext, watch: em.Watch[m.Devices], device: m.Device, trigger: m.Trigger) -> m.Trigger:
    react = replace(trigger, log_source=Log.REACT)
    ctx.log.write(_line(ctx, watch, device), react)
    return react


def _line(ctx: m.AppContext, watch: em.Watch[m.Devices], device: m.Device) -> str:
    state = ctx.plugin_state[orc_extras.react]
    command = watch.rule.steps[0].command
    return f"`{device.name}` {_trigger_label(watch, state)} → set {_targets(command.subject)} {command.value}"


def _command(watch: em.Watch[m.Devices]) -> m.DeviceCommand:
    command = watch.rule.steps[0].command
    return em.Command(command.subject, command.value, tag=em.Always())


def _run_react(payload: tuple[em.Defer[m.Devices], m.Device], *, ctx: m.AppContext) -> None:
    deferred, device = payload
    ctx.plugin_state[orc_extras.react].delayed.pop(deferred.watch, None)
    match ctx.engine.evaluate((deferred,), read=_reader(ctx), force=True):
        case (em.Report(em.Watch() as watch, commands),) if commands:
            ctx.plugin_state[orc_extras.react].fired[watch] = ctx.api.local_now()
            _dispatch(ctx, watch, device)
