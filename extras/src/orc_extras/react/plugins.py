import sys
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, NamedTuple

from apscheduler.triggers.date import DateTrigger

from orc import model as m
from orc.kernel import engine
from orc.plugins import requires_ctx

JOB_ID = "react"
COOLDOWN = timedelta(seconds=10)  # a (rule, device) won't re-fire within this window — breaks flapping loops

TRIGGERS = {"on": "switch", "off": "switch", "open": "contact", "closed": "contact"}


class React(NamedTuple):
    rules: dict[int, engine.Rule[m.Devices]]
    sources: dict[str, m.DeviceEnum]


class Log(m.LogSourceEnum):
    REACT = "react"


class When(NamedTuple):
    device: m.DeviceEnum
    state: str | m.AcState | m.Playback


@dataclass(frozen=True)
class AcIs:
    channel: m.AcChannel
    allowed: m.AcState

    def holds(self, read: engine.Read) -> bool:
        current = read(self.channel)
        return isinstance(current, m.AcState) and current in self.allowed

    @property
    def channels(self) -> tuple[engine.Channel, ...]:
        return (self.channel,)


def condition(when: When | None) -> engine.Condition:
    if when is None:
        return engine.ALWAYS
    elif isinstance(when.state, m.AcState):
        return AcIs(m.AcChannel(when.device), when.state)
    elif isinstance(when.state, m.Playback):
        return engine.Is(m.CastChannel(when.device), when.state)
    else:
        return engine.Is(m.MqttDeviceChannel(when.device, TRIGGERS[when.state]), when.state)


def source_of(rule: engine.Rule[m.Devices]) -> m.DeviceEnum:
    channel = _transition(rule).channel
    assert isinstance(channel, m.MqttDeviceChannel)
    return channel.device


def _transition(rule: engine.Rule[m.Devices]) -> engine.Transition:
    trigger = rule.trigger
    assert isinstance(trigger, engine.Transition)
    return trigger


def _reader(ctx: m.AppContext) -> engine.Read:
    def read(channel: engine.Channel) -> engine.Value:
        match channel:
            case m.MqttDeviceChannel(device, attribute):
                target = str(device.value)
                found = next((s for s in ctx.api.device_states() if str(s.id) == target or s.name == target), None)
                return found.attributes.get(attribute) if found else None
            case m.AcChannel(device):
                status = next((s for s in ctx.api.capture_acs() if s.what is device), None)
                return status.state if status else None
            case m.CastChannel(device):
                sound = next((s for s in ctx.api.capture_sounds() if s.what is device), None)
                return sound.playback if sound else None
            case _:
                raise KeyError(channel)

    return read


def _state(ctx: m.AppContext) -> React:
    state = ctx.plugin_state[sys.modules[__name__]]
    assert isinstance(state, React)
    return state


def _on_event(ctx: m.AppContext, device: m.DeviceState, attribute: str, old: Any, new: Any) -> None:
    state = _state(ctx)
    source = state.sources.get(str(device.id)) or state.sources.get(device.name)
    if source is None:
        return
    event = engine.Event(m.MqttDeviceChannel(source, attribute), old, new)
    for reaction in ctx.engine.on_event(event, ctx.api.local_now(), _reader(ctx)):
        match reaction:
            case engine.Report():
                _report(ctx, reaction, device.name, "")
            case engine.Deferred():
                ctx.scheduler.add_job(
                    _run_react,
                    DateTrigger(reaction.when, timezone=ctx.config.settings.tz),
                    name=f"React {device.name}",
                    id=f"{JOB_ID}-{reaction.key}",
                    replace_existing=True,
                    jobstore=ctx.api.JOBSTORE_MEMORY,
                    args=(reaction, device.name),
                )
            case engine.Cancel():
                job_id = f"{JOB_ID}-{reaction.key}"
                if ctx.scheduler.get_job(job_id, jobstore=ctx.api.JOBSTORE_MEMORY):
                    ctx.scheduler.remove_job(job_id, jobstore=ctx.api.JOBSTORE_MEMORY)


def _targets(what: m.Devices) -> str:
    return ", ".join(f"`{d.label or d.name}`" for d in what.all())


def _wanted(cond: engine.Condition) -> Any:
    if isinstance(cond, AcIs):
        return (cond.allowed.name or "").lower()
    assert isinstance(cond, engine.Is)
    return cond.value.value if isinstance(cond.value, m.Playback) else cond.value


def _unmet(cond: engine.Condition) -> str:
    channel = cond.channels[0]
    assert isinstance(channel, m.AcChannel | m.MqttDeviceChannel | m.CastChannel)
    return f"`{channel.device.label or channel.device.name}` is not {_wanted(cond)}"


def _apply(ctx: m.AppContext, what: m.Devices, action: Any, entry: m.LogEntry) -> None:
    ctx.api.dispatch((engine.Command(what, action, tag=m.Trigger.SYSTEM),), entry=entry)


def _report(ctx: m.AppContext, report: engine.Report, name: str, note: str) -> None:
    rule = _state(ctx).rules[report.key]
    state = _transition(rule).value
    command = rule.items[0].command
    if report.disposition is engine.Disposition.FIRED:
        entry = ctx.api.log(Log.REACT, f"`{name}` {state}{note} → set {_targets(command.channel)} {command.value}")
        _apply(ctx, command.channel, command.value, entry)
    elif report.disposition is engine.Disposition.COOLED:
        assert report.since is not None
        ctx.api.log(Log.REACT, f"`{name}` {state}{note} — skipped, rule fired {int(report.since.total_seconds())}s ago (cooldown)")
    else:
        ctx.api.log(Log.REACT, f"`{name}` {state}{note} — skipped, {_unmet(rule.items[0].condition)}")


@requires_ctx
def _run_react(deferred: engine.Deferred, name: str, *, ctx: m.AppContext) -> None:
    report = ctx.engine.on_fire(deferred, ctx.api.local_now(), _reader(ctx))
    rule = _state(ctx).rules[report.key]
    _report(ctx, report, name, f" {int(rule.delay.total_seconds() // 60)}m ago")
