from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, ClassVar, NamedTuple

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
        return new == self.to and old not in (None, self.to)


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
    disabled: dict[str, datetime] = field(default_factory=dict)
    delayed: dict[em.Watch[Any], datetime] = field(default_factory=dict)
    fired: dict[em.Watch[Any], datetime] = field(default_factory=dict)


@dataclass(frozen=True)
class AcIs(em.Condition):
    subject: AcSubject
    state: m.AcState

    def holds(self, world: em.World) -> bool:
        return world.read(self.subject) == self.state


@dataclass(frozen=True)
class FormulaSubject(em.Subject):
    function: Callable[..., float | None]
    args: tuple[Any, ...]


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
        number = _number(value)
        return number is not None and self.low <= number < self.high


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except ValueError:
        return None


@dataclass(eq=False)
class _HysteresisEdge(em.Condition):
    """A Schmitt trigger that is true only on the reading that flips it.

    It starts undecided, so a restart inside the dead band fires nothing. It remembers its state, so each instance
    is identified by object, not by value. A crossing a rule's cooldown holds back is dropped, not offered again.
    """

    subject: em.Subject
    high: float
    low: float
    _above: bool | None = field(default=None, init=False, repr=False)
    _fires_on: ClassVar[tuple[bool, bool]]

    def __post_init__(self) -> None:
        if self.high == self.low:
            raise ValueError(f"{type(self).__name__}: high and low are both {self.high}; they need a dead band between them")
        self.high, self.low = max(self.high, self.low), min(self.high, self.low)

    def holds(self, world: em.World) -> bool:
        value = _number(world.read(self.subject))
        if value is None:
            return False
        before = self._above
        if value >= self.high:
            self._above = True
        elif value < self.low:
            self._above = False
        return (before, self._above) == self._fires_on


class CrossedUp(_HysteresisEdge):
    """True when the reading rises to `high`, once it has been below `low`."""

    _fires_on = (False, True)


class CrossedDown(_HysteresisEdge):
    """True when the reading falls below `low`, once it has been at or above `high`."""

    _fires_on = (True, False)


@dataclass(frozen=True)
class Present(em.Condition):
    names: tuple[str, ...]

    def holds(self, world: em.World) -> bool:
        return any(world.read(m.PersonSubject(name)) for name in self.names)
