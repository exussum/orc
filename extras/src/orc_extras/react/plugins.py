from typing import Any

from apscheduler.triggers.date import DateTrigger

from orc import model as m
from orc.kernel import cast, engine
from orc.plugins import requires_ctx
from orc.security import safe_eval
from orc_extras.react.model import FUNCTIONS, DeviceChanged, Formula, Log, source_of

JOB_ID = "react"


def _trigger_label(rule: engine.Rule) -> Any:
    trigger = rule.trigger
    if isinstance(trigger, engine.Transition):
        return trigger.value
    return cast.instance(trigger, DeviceChanged).expr


def _reader(ctx: m.AppContext) -> engine.Read:
    world_read = ctx.api.world_reader()

    def read(channel: engine.Channel) -> engine.Value:
        match channel:
            case m.MqttDeviceChannel(device, attribute):
                found = next((s for s in ctx.api.device_states() if s.id == device.value), None)
                return found.attributes.get(attribute) if found else None
            case m.AcChannel(device):
                status = next((s for s in ctx.api.capture_acs() if s.what is device), None)
                return status.state if status else None
            case m.CastChannel(device):
                sound = next((s for s in ctx.api.capture_sounds() if s.what is device), None)
                return sound.playback if sound else None
            case Formula(device, expr):
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
                return world_read(channel)

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
    event = engine.Event(m.MqttDeviceChannel(source, attribute), old, new)
    fired: list[engine.Report] = []
    for reaction in ctx.engine.on_event(event, ctx.api.local_now(), _reader(ctx)):
        match reaction:
            case engine.Report() if reaction.disposition is engine.Disposition.FIRED:
                fired.append(reaction)
            case engine.Deferred():
                ctx.scheduler.add_job(
                    _run_react,
                    DateTrigger(reaction.when, timezone=ctx.config.settings.tz),
                    name=f"React {device.name}",
                    id=f"{JOB_ID}-{hash(reaction.rule)}",
                    replace_existing=True,
                    jobstore=ctx.api.JOBSTORE_MEMORY,
                    args=(reaction, device.name),
                )
            case engine.Cancel():
                job_id = f"{JOB_ID}-{hash(reaction.rule)}"
                if ctx.scheduler.get_job(job_id, jobstore=ctx.api.JOBSTORE_MEMORY):
                    ctx.scheduler.remove_job(job_id, jobstore=ctx.api.JOBSTORE_MEMORY)
    if fired:
        entries = [_log(ctx, report, device.name, "") for report in fired]
        ctx.api.dispatch(ctx.api.squish(map(_command, fired), entries[0]), entry=entries[0])


def _targets(what: engine.Channel) -> str:
    return ", ".join(f"`{d.label or d.name}`" for d in cast.instance(what, m.Devices).all())


def _dispatch(ctx: m.AppContext, report: engine.Report, name: str, note: str) -> None:
    if report.disposition is not engine.Disposition.FIRED:
        return
    ctx.api.dispatch((_command(report),), entry=_log(ctx, report, name, note))


def _log(ctx: m.AppContext, report: engine.Report, name: str, note: str) -> m.LogEntry:
    command = report.rule.items[0].command
    return ctx.api.log(
        Log.REACT,
        f"`{name}` {_trigger_label(report.rule)}{note} → set {_targets(command.channel)} {command.value}",
        m.Broker(id=str(source_of(report.rule).value), source="hubitat"),
    )


def _command(report: engine.Report) -> m.DeviceCommand:
    command = report.rule.items[0].command
    return engine.Command(cast.instance(command.channel, m.Devices), command.value, tag=m.Tag.SYSTEM)


@requires_ctx
def _run_react(deferred: engine.Deferred, name: str, *, ctx: m.AppContext) -> None:
    report = ctx.engine.on_fire(deferred, ctx.api.local_now(), _reader(ctx))
    _dispatch(ctx, report, name, f" {int(deferred.rule.delay.total_seconds() // 60)}m ago")
