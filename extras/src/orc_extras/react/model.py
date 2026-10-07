from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, NamedTuple

from orc_engine import model as em

from orc import model as m

DEFAULT_PAUSE = timedelta(minutes=10)
COOLDOWN = timedelta(seconds=10)  # a (rule, device) won't re-fire within this window — breaks flapping loops

TRIGGERS = {"on": "switch", "off": "switch", "open": "contact", "closed": "contact", "active": "motion", "inactive": "motion"}


class Log(m.LogSourceEnum):
    REACT = "react"


@dataclass(frozen=True)
class Device(em.Subject):
    device: m.DeviceEnum


@dataclass(frozen=True)
class MqttDeviceSubject(em.Subject):
    device: m.DeviceEnum
    attribute: str


@dataclass(frozen=True)
class AcSubject(em.Subject):
    device: m.DeviceEnum


@dataclass(frozen=True)
class CastSubject(em.Subject):
    device: m.DeviceEnum


@dataclass(frozen=True)
class Transition(em.Condition):
    subject: MqttDeviceSubject
    to: em.Value

    def holds(self, world: em.World) -> bool:
        change = world.changed(self.subject)
        if change is None:
            return False
        old, new = change
        return new == self.to and old != self.to


class Reaction(NamedTuple):
    watch: em.Watch[m.Devices]
    pause: timedelta
    name: str
    source: m.DeviceEnum


class Group(NamedTuple):
    watches: tuple[em.Watch[m.Devices], ...]
    pause: timedelta


@dataclass
class State:
    watches: tuple[em.Watch[m.Devices], ...]
    groups: dict[str, Group]
    name_of: dict[em.Watch[Any], str]
    functions: dict[str, Callable[..., float]] = field(default_factory=dict)  # a formula's vocabulary, bound at setup
    disabled: dict[str, datetime] = field(default_factory=dict)


@dataclass(frozen=True)
class AcIs(em.Condition):
    subject: AcSubject
    state: str  # on, off, or a mode

    def holds(self, world: em.World) -> bool:
        current = world.read(self.subject)
        if not isinstance(current, m.AcState):
            return False
        elif self.state in (m.ON, m.OFF):
            return current.power == self.state
        return current.power == m.ON and current.mode == self.state


@dataclass(frozen=True)
class FormulaSubject(em.Subject):
    device: m.DeviceEnum
    expr: str


@dataclass(frozen=True)
class Range(em.Condition):
    subject: em.Subject
    low: float
    high: float
    edge: bool = False

    def holds(self, world: em.World) -> bool:
        if not self.edge:
            return self._inside(world.read(self.subject))
        change = world.changed(self.subject)
        if change is None:
            return False
        old, new = change
        return self._inside(new) and not self._inside(old)

    def _inside(self, value: Any) -> bool:
        if not isinstance(value, (int, float, str)):
            return False
        try:
            return self.low <= float(value) < self.high
        except ValueError:
            return False


@dataclass(frozen=True)
class Present(em.Condition):
    names: tuple[str, ...]

    def holds(self, world: em.World) -> bool:
        return any(world.read(m.PersonSubject(name)) for name in self.names)
