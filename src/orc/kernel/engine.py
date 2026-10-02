"""A generic rule evaluator: a rule's steps apply when their conditions hold.

Self-contained by design — no orc imports — so the whole file can be lifted out as a
standalone package later. The host (orc) supplies the world as a `Read` callback and the
clock as a `now` argument, and performs the returned commands. `Runtime` holds in-memory
cooldown and snapshot state and reads no clock, calls no scheduler, and starts no threads.
"""

from collections.abc import Callable, Collection, Hashable, Iterable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from threading import RLock
from typing import Any, NamedTuple, Protocol, overload, runtime_checkable


class Subject:
    pass


@runtime_checkable
class DeviceSubject(Protocol):
    def one(self) -> Any: ...


type Value = Hashable
type Read = Callable[[Subject], Value]


class ClockSubject(Subject):
    pass


CLOCK = ClockSubject()


@dataclass(frozen=True)
class Command[T = None, C: Subject = Subject]:
    subject: C
    value: Value
    tag: T | None = None


class Condition(Protocol):
    def holds(self, read: Read) -> bool: ...


@dataclass(frozen=True)
class Never:
    def holds(self, read: Read) -> bool:
        return False


NEVER = Never()


@dataclass(frozen=True)
class Is:
    subject: Subject
    value: Value

    def holds(self, read: Read) -> bool:
        return read(self.subject) == self.value


@dataclass(frozen=True)
class In:
    subject: Subject
    value: Value

    def holds(self, read: Read) -> bool:
        values = read(self.subject)
        if not isinstance(values, Collection):
            raise TypeError(f"{self.subject} read {values!r}, not a collection")
        return self.value in values


@dataclass(frozen=True)
class Has:
    subject: Subject

    def holds(self, read: Read) -> bool:
        return read(self.subject) is not None


@dataclass(frozen=True)
class During:
    start: time
    stop: time

    def holds(self, read: Read) -> bool:
        now = _clock(read).time()
        if self.start <= self.stop:
            return self.start <= now < self.stop
        return now >= self.start or now < self.stop


class Step[C: Subject = Subject](NamedTuple):
    conditions: tuple[Condition, ...]
    command: Command[Any, C]

    def holds(self, read: Read) -> bool:
        return all(cond.holds(read) for cond in self.conditions)


@dataclass(frozen=True)
class Rule[C: Subject = Subject]:
    steps: tuple[Step[C], ...]
    name: str = ""
    tags: frozenset[str] = frozenset()

    @property
    def commands(self) -> tuple[Command[Any, C], ...]:
        return tuple(step.command for step in self.steps)

    def holds(self, read: Read) -> bool:
        return any(step.holds(read) for step in self.steps)

    def where(self, keep: Callable[[Command[Any, C]], bool]) -> Rule[C]:
        return Rule(tuple(step for step in self.steps if keep(step.command)), self.name, self.tags)


@dataclass(frozen=True)
class Action[C: Subject = Subject]:
    commands: tuple[Command[Any, C], ...] = ()


@dataclass(frozen=True)
class Automation[C: Subject = Subject]:
    trigger: Condition
    rule: Rule[C]
    delay: timedelta = timedelta()
    cooldown: timedelta = timedelta()
    cancel: Condition = NEVER


class Report[C: Subject = Subject](NamedTuple):
    item: Item[C]
    commands: tuple[Command[Any, C], ...]


@dataclass(frozen=True)
class Deferred[C: Subject = Subject]:
    automation: Automation[C]
    when: datetime


@dataclass(frozen=True)
class Cancel[C: Subject = Subject]:
    automation: Automation[C]


type Item[C: Subject = Subject] = Rule[C] | Action[C] | Automation[C] | Deferred[C]
type Outcome[C: Subject = Subject] = Report[C] | Deferred[C] | Cancel[C]


class SnapShot[C: Subject = Subject](NamedTuple):
    routine: tuple[Command[Any, C], ...]
    end: datetime
    label: str = ""


def _clock(read: Read) -> datetime:
    told = read(CLOCK)
    if not isinstance(told, datetime):
        raise TypeError(f"{CLOCK} read {told!r}, not a datetime")
    return told


def _layered(first: Read, then: Read) -> Read:
    def read(subject: Subject) -> Value:
        try:
            return first(subject)
        except KeyError:
            return then(subject)

    return read


class Runtime:
    def __init__(self, read: Read, *, bypass: Any = None, override_key: str | None = None) -> None:
        self._read = read
        self._snapshots: dict[str, tuple[Any, datetime]] = {}
        self._last_fired: dict[Automation[Any], datetime] = {}
        self._lock = RLock()
        self._bypass = bypass
        self._override_key = override_key

    def save_snapshot(self, key: str, payload: Any, deadline: datetime) -> None:
        with self._lock:
            self._snapshots[key] = (payload, deadline)

    def snapshot_active(self, key: str) -> bool:
        with self._lock:
            entry = self._snapshots.get(key)
            return bool(entry and _clock(self._read) <= entry[1])

    def read_snapshot(self, key: str) -> Any:
        with self._lock:
            entry = self._snapshots.get(key)
            return entry[0] if entry and _clock(self._read) <= entry[1] else None

    def pop_snapshot(self, key: str) -> Any:
        with self._lock:
            entry = self._snapshots.pop(key, None)
            return entry[0] if entry and _clock(self._read) <= entry[1] else None

    def snapshots(self) -> dict[str, Any]:
        with self._lock:
            now = _clock(self._read)
            return {key: payload for key, (payload, deadline) in self._snapshots.items() if now <= deadline}

    @overload
    def evaluate[C: Subject](
        self, items: Iterable[Rule[C] | Action[C] | Deferred[C]], *, read: Read, force: bool
    ) -> tuple[Report[C], ...]: ...

    @overload
    def evaluate[C: Subject](self, items: Iterable[Item[C]], *, read: Read, force: bool) -> tuple[Outcome[C], ...]: ...

    def evaluate[C: Subject](self, items: Iterable[Item[C]], *, read: Read, force: bool) -> tuple[Outcome[C], ...]:
        with self._lock:
            read = _layered(read, self._read)
            now = _clock(read)
            out: list[Outcome[C]] = []
            for item in items:
                match item:
                    case Deferred(automation, _):
                        out.append(Report(item, self._applied_once(automation, now, read, force)))
                    case Automation():
                        out.append(self._automated(item, now, read, force))
                    case Action(plain):
                        out.append(Report(item, self._applied(Rule(tuple(Step((), c) for c in plain)), now, read, force)))
                    case _:
                        out.append(Report(item, self._applied(item, now, read, force)))
            return tuple(out)

    def override_scene(self, ctx: Any, name: str, commands: tuple[Command[Any], ...], end: datetime, label: str, entry: Any) -> None:
        if not self.snapshot_active(name):
            self.save_snapshot(name, SnapShot(ctx.api.capture_lights(), end, label), end)
            captured = self.read_snapshot(name).routine
            items = ", ".join(f"`{_one_name(c.subject)}`={c.value}" for c in captured if c.value != ctx.api.m.OFF)
            entry.add(entry.source, ctx.api.Log.SNAPSHOT_TAKEN.format(name=label, end=end, items=items or ctx.api.Log.SNAPSHOT_ALL_OFF))
        ctx.api.dispatch(commands, force=True, entry=entry)

    def restore_scene(self, ctx: Any, name: str, commands: tuple[Command[Any], ...], entry: Any) -> None:
        snapshot = self.pop_snapshot(name)
        if snapshot:
            commands = snapshot.routine
            entry.add(entry.source, ctx.api.Log.SNAPSHOT_RESTORED.format(name=snapshot.label))
        ctx.api.dispatch(commands, force=True, entry=entry)

    def _automated[C: Subject](self, automation: Automation[C], now: datetime, read: Read, force: bool) -> Outcome[C]:
        if not automation.trigger.holds(read):
            if automation.delay and automation.cancel.holds(read):
                return Cancel(automation)
            return Report(automation, ())
        if automation.delay:
            return Deferred(automation, now + automation.delay)
        return Report(automation, self._applied_once(automation, now, read, force))

    def _applied_once[C: Subject](self, automation: Automation[C], now: datetime, read: Read, force: bool) -> tuple[Command[Any, C], ...]:
        last = self._last_fired.get(automation)
        if automation.cooldown and last is not None and now - last < automation.cooldown:
            return ()
        commands = self._applied(automation.rule, now, read, force)
        if commands:
            self._last_fired[automation] = now
        return commands

    def _applied[C: Subject](self, rule: Rule[C], now: datetime, read: Read, force: bool) -> tuple[Command[Any, C], ...]:
        override_key = self._override_key
        snapshot = self._snapshots.get(override_key) if override_key is not None else None
        active = snapshot is not None and now <= snapshot[1]
        out: list[Command[Any, C]] = []
        for step in rule.steps:
            if not step.holds(read):
                continue
            command = step.command
            if not force:
                if command.tag == self._bypass:
                    if override_key is not None and snapshot is not None and active:
                        payload, deadline = snapshot
                        merged = {c.subject: c for c in payload.routine}
                        merged[command.subject] = command
                        self._snapshots[override_key] = (payload._replace(routine=tuple(merged.values())), deadline)
                elif active:
                    continue
            out.append(command)
        return tuple(out)


def _one_name(subject: Subject) -> str:
    if not isinstance(subject, DeviceSubject):
        raise TypeError(f"{subject} is not a device subject")
    return str(subject.one().name)
