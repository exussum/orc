import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import NamedTuple

from orc import model as m
from orc.kernel import cast, engine

DEFAULT_PAUSE = timedelta(minutes=10)
COOLDOWN = timedelta(seconds=10)  # a (rule, device) won't re-fire within this window — breaks flapping loops

TRIGGERS = {"on": "switch", "off": "switch", "open": "contact", "closed": "contact", "active": "motion", "inactive": "motion"}


def _dewpoint(temp_f: float, humidity: float) -> float:
    temp_c = (temp_f - 32) * 5 / 9
    gamma = math.log(humidity / 100.0) + (17.62 * temp_c) / (243.12 + temp_c)
    dewpoint_c = (243.12 * gamma) / (17.62 - gamma)
    return dewpoint_c * 9 / 5 + 32


FUNCTIONS = {"dewpoint": _dewpoint}


class Log(m.LogSourceEnum):
    REACT = "react"


class Reaction(NamedTuple):
    rule: engine.Rule[m.Devices]
    pause: timedelta
    name: str


@dataclass
class State:
    pauses: dict[engine.Rule[m.Devices], timedelta]
    names: dict[engine.Rule[m.Devices], str] = field(default_factory=dict)
    disabled: dict[engine.Rule[m.Devices], datetime] = field(default_factory=dict)

    def disable(self, rule: engine.Rule[m.Devices], now: datetime) -> datetime:
        self.disabled[rule] = now + self.pauses[rule]
        return self.disabled[rule]

    def enable(self, rule: engine.Rule[m.Devices]) -> None:
        self.disabled.pop(rule, None)

    def named(self) -> dict[str, list[engine.Rule[m.Devices]]]:
        groups: dict[str, list[engine.Rule[m.Devices]]] = {}
        for rule in self.pauses:
            groups.setdefault(self.names[rule], []).append(rule)
        return groups

    def is_disabled(self, rule: engine.Rule[m.Devices], now: datetime) -> bool:
        return self.disabled_until(rule, now) is not None

    def disabled_until(self, rule: engine.Rule[m.Devices], now: datetime) -> datetime | None:
        until = self.disabled.get(rule)
        if until and until > now:
            return until
        self.disabled.pop(rule, None)
        return None


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
    expr: str

    def fired(self, event: engine.Event) -> bool:
        return isinstance(event.channel, m.MqttDeviceChannel) and event.channel.device == self.device


@dataclass(unsafe_hash=True)
class Range:
    channel: engine.Channel
    low: float
    high: float
    last_measurement: float | None = field(hash=False, default=None)

    def holds(self, read: engine.Read) -> bool:
        value = read(self.channel)
        if not isinstance(value, (int, float, str)):
            return False
        try:
            result = self.low <= float(value) <= self.high and (
                self.last_measurement is None or not self.low <= self.last_measurement <= self.high
            )
            self.last_measurement = float(value)
            return result
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


def source_of(rule: engine.Rule) -> m.DeviceEnum:
    trigger = rule.trigger
    if isinstance(trigger, engine.Transition):
        return cast.instance(trigger.channel, m.MqttDeviceChannel).device
    return cast.instance(trigger, DeviceChanged).device
