import math
import sys
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, NamedTuple

from apscheduler.triggers.date import DateTrigger

from orc import model as m
from orc.kernel import engine
from orc.plugins import requires_ctx
from orc.security import safe_eval

JOB_ID = "react"
COOLDOWN = timedelta(seconds=10)  # a (rule, device) won't re-fire within this window — breaks flapping loops

TRIGGERS = {"on": "switch", "off": "switch", "open": "contact", "closed": "contact", "active": "motion", "inactive": "motion"}


def _dewpoint(temp_f: float, humidity: float) -> float:
    temp_c = (temp_f - 32) * 5 / 9
    gamma = math.log(humidity / 100.0) + (17.62 * temp_c) / (243.12 + temp_c)
    return (243.12 * gamma) / (17.62 - gamma)


FUNCTIONS = {"dewpoint": _dewpoint}


class React(NamedTuple):
    rules: dict[int, engine.Rule[m.Devices]]
    sources: dict[int, m.DeviceEnum]


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


@dataclass(frozen=True)
class Formula(engine.Channel):
    device: m.DeviceEnum
    expr: str


@dataclass(frozen=True)
class DeviceChanged:
    device: m.DeviceEnum

    def fired(self, event: engine.Event) -> bool:
        channel = event.channel
        return isinstance(channel, m.MqttDeviceChannel) and channel.device == self.device


@dataclass(frozen=True)
class Range:
    channel: engine.Channel
    low: float
    high: float

    def holds(self, read: engine.Read) -> bool:
        value = read(self.channel)
        if not isinstance(value, (int, float, str)):
            return False
        try:
            return self.low <= float(value) <= self.high
        except ValueError:
            return False

    @property
    def channels(self) -> tuple[engine.Channel, ...]:
        return (self.channel,)


@dataclass(frozen=True)
class Present:
    names: tuple[str, ...]

    def holds(self, read: engine.Read) -> bool:
        return any(read(m.PersonChannel(name)) for name in self.names)

    @property
    def channels(self) -> tuple[engine.Channel, ...]:
        return tuple(m.PersonChannel(name) for name in self.names)


def condition(when: When | None) -> tuple[engine.Condition, ...]:
    if when is None:
        return ()
    elif isinstance(when.state, m.AcState):
        return (AcIs(m.AcChannel(when.device), when.state),)
    elif isinstance(when.state, m.Playback):
        return (engine.Is(m.CastChannel(when.device), when.state),)
    else:
        return (engine.Is(m.MqttDeviceChannel(when.device, TRIGGERS[when.state]), when.state),)


def source_of(rule: engine.Rule[m.Devices]) -> m.DeviceEnum:
    trigger = rule.trigger
    if isinstance(trigger, engine.Transition):
        assert isinstance(trigger.channel, m.MqttDeviceChannel)
        return trigger.channel.device
    assert isinstance(trigger, DeviceChanged)
    return trigger.device


def _trigger_label(rule: engine.Rule[m.Devices]) -> Any:
    trigger = rule.trigger
    if isinstance(trigger, engine.Transition):
        return trigger.value
    assert isinstance(trigger, DeviceChanged)
    condition = rule.items[0].conditions[0]
    assert isinstance(condition, Range)
    channel = condition.channel
    assert isinstance(channel, Formula)
    return channel.expr


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


def _state(ctx: m.AppContext) -> React:
    state = ctx.plugin_state[sys.modules[__name__]]
    assert isinstance(state, React)
    return state


def _on_event(ctx: m.AppContext, device: m.DeviceState, attribute: str, old: Any, new: Any) -> None:
    state = _state(ctx)
    source = state.sources.get(device.id)
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


def _unmet(conditions: tuple[engine.Condition, ...]) -> str:
    return " and ".join(_describe(cond) for cond in conditions)


def _describe(cond: engine.Condition) -> str:
    if isinstance(cond, Range):
        channel = cond.channel
        assert isinstance(channel, Formula)
        return f"`{channel.device.label or channel.device.name}` {channel.expr} not in {cond.low}-{cond.high}"
    elif isinstance(cond, Present):
        return f"nobody in {', '.join(cond.names)} is home"
    elif isinstance(cond, engine.Is) and isinstance(cond.channel, m.AnyoneChannel):
        return "nobody is home"
    channel = cond.channels[0]
    assert isinstance(channel, m.AcChannel | m.MqttDeviceChannel | m.CastChannel)
    return f"`{channel.device.label or channel.device.name}` is not {_wanted(cond)}"


def _apply(ctx: m.AppContext, what: m.Devices, action: Any, entry: m.LogEntry) -> None:
    ctx.api.dispatch((engine.Command(what, action, tag=m.Trigger.SYSTEM),), entry=entry)


def _report(ctx: m.AppContext, report: engine.Report, name: str, note: str) -> None:
    rule = _state(ctx).rules[report.key]
    state = _trigger_label(rule)
    command = rule.items[0].command
    if report.disposition is engine.Disposition.FIRED:
        entry = ctx.api.log(Log.REACT, f"`{name}` {state}{note} → set {_targets(command.channel)} {command.value}")
        _apply(ctx, command.channel, command.value, entry)
    elif report.disposition is engine.Disposition.COOLED:
        assert report.since is not None
        ctx.api.log(Log.REACT, f"`{name}` {state}{note} — skipped, rule fired {int(report.since.total_seconds())}s ago (cooldown)")
    else:
        ctx.api.log(Log.REACT, f"`{name}` {state}{note} — skipped, {_unmet(rule.items[0].conditions)}")


@requires_ctx
def _run_react(deferred: engine.Deferred, name: str, *, ctx: m.AppContext) -> None:
    report = ctx.engine.on_fire(deferred, ctx.api.local_now(), _reader(ctx))
    rule = _state(ctx).rules[report.key]
    _report(ctx, report, name, f" {int(rule.delay.total_seconds() // 60)}m ago")
