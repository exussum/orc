import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, NamedTuple

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


@dataclass(frozen=True)
class ChangeChannel(engine.Channel):
    channel: engine.Channel


@dataclass(frozen=True)
class Transition:
    channel: m.MqttDeviceChannel
    to: engine.Value

    def holds(self, read: engine.Read) -> bool:
        change = read(ChangeChannel(self.channel))
        old, new = cast.instance(change, tuple) if change else (None, None)
        return new == self.to and old != self.to


class Reaction(NamedTuple):
    automation: engine.Automation[m.Devices]
    pause: timedelta
    name: str


class Group(NamedTuple):
    rules: tuple[engine.Rule[m.Devices], ...]
    pause: timedelta


@dataclass
class State:
    automations: tuple[engine.Automation[m.Devices], ...]
    groups: dict[str, Group]
    name_of: dict[engine.Rule[Any], str]
    disabled: dict[str, datetime] = field(default_factory=dict)


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


@dataclass(frozen=True)
class FormulaChannel(engine.Channel):
    device: m.DeviceEnum
    expr: str


@dataclass(frozen=True)
class DeviceChanged:
    device: m.DeviceEnum
    expr: str

    def holds(self, read: engine.Read) -> bool:
        return read(ChangeChannel(m.Devices(self.device))) is not None


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


@dataclass(frozen=True)
class Present:
    names: tuple[str, ...]

    def holds(self, read: engine.Read) -> bool:
        return any(read(m.PersonChannel(name)) for name in self.names)


def condition(when: When | None) -> tuple[engine.Condition, ...]:
    if when is None:
        return ()
    elif isinstance(when.state, m.AcState):
        return (AcIs(m.AcChannel(when.device), when.state),)
    elif isinstance(when.state, m.Playback):
        return (engine.Is(m.CastChannel(when.device), when.state),)
    else:
        return (engine.Is(m.MqttDeviceChannel(when.device, TRIGGERS[when.state]), when.state),)


def source_of(automation: engine.Automation[Any]) -> m.DeviceEnum:
    trigger = automation.trigger
    if isinstance(trigger, Transition):
        return trigger.channel.device
    return cast.instance(trigger, DeviceChanged).device
