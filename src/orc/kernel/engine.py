"""A generic rule engine: on a trigger edge, if a condition holds, emit commands.

Self-contained by design — no orc imports — so the whole file can be lifted out as a
standalone package later. The host (orc) supplies the world as a `Read` callback, the clock
as a `now` argument, and performs the returned commands and `Reaction`s.
`Runtime` holds in-memory cooldown and snapshot state and reads no clock, calls no
scheduler, and starts no threads — it decides and returns instructions the host acts on.
"""

from collections.abc import Callable, Collection, Hashable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import Enum, auto
from threading import RLock
from typing import Any, NamedTuple, Protocol, cast


class Channel:
    pass


type Value = Hashable
type Read = Callable[[Channel], Value]


def _no_read(_channel: Channel) -> Value:
    return None


@dataclass(frozen=True)
class Command[T = None, C: Channel = Channel]:
    channel: C
    value: Value
    tag: T | None = None


@dataclass(frozen=True)
class Event:
    channel: Channel
    prev: Value
    now: Value


class Trigger(Protocol):
    def fired(self, event: Event) -> bool: ...


class Condition(Protocol):
    def holds(self, read: Read) -> bool: ...

    @property
    def channels(self) -> tuple[Channel, ...]: ...


@dataclass(frozen=True)
class Transition:
    channel: Channel
    value: Value

    def fired(self, event: Event) -> bool:
        return event.channel == self.channel and event.now == self.value


@dataclass(frozen=True)
class Changed:
    channels: tuple[Channel, ...]

    def fired(self, event: Event) -> bool:
        return event.channel in self.channels


@dataclass(frozen=True)
class Never:
    def fired(self, event: Event) -> bool:
        return False


NEVER = Never()


@dataclass(frozen=True)
class At:
    when: time | str

    def fired(self, event: Event) -> bool:
        return False


@dataclass(frozen=True)
class Is:
    channel: Channel
    value: Value

    def holds(self, read: Read) -> bool:
        return read(self.channel) == self.value

    @property
    def channels(self) -> tuple[Channel, ...]:
        return (self.channel,)


@dataclass(frozen=True)
class In:
    channel: Channel
    value: Value

    def holds(self, read: Read) -> bool:
        return self.value in cast("Collection[Value]", read(self.channel))

    @property
    def channels(self) -> tuple[Channel, ...]:
        return (self.channel,)


class Clause[C: Channel = Channel](NamedTuple):
    conditions: tuple[Condition, ...]
    command: Command[Any, C]


@dataclass(frozen=True)
class Rule[C: Channel = Channel]:
    trigger: Trigger
    items: tuple[Clause[C], ...]
    delay: timedelta = timedelta()
    cooldown: timedelta = timedelta()
    name: str = ""
    tags: frozenset[str] = frozenset()

    @property
    def commands(self) -> tuple[Command[Any, C], ...]:
        return tuple(clause.command for clause in self.items)


class Disposition(Enum):
    FIRED = auto()
    COOLED = auto()
    BLOCKED = auto()


@dataclass(frozen=True)
class Report:
    key: int
    disposition: Disposition
    since: timedelta | None = None


@dataclass(frozen=True)
class Deferred:
    key: int
    rule: Rule
    when: datetime


@dataclass(frozen=True)
class Cancel:
    key: int


type Reaction = Report | Deferred | Cancel


class SnapShot[C: Channel = Channel](NamedTuple):
    routine: tuple[Command[Any, C], ...]
    end: datetime
    label: str = ""


class Runtime:
    def __init__(self, rules: Sequence[Rule] = (), *, bypass: Any = None, override_key: str | None = None) -> None:
        self._rules: list[Rule] = []
        self._last_fired: dict[int, datetime] = {}
        self._snapshots: dict[str, tuple[Any, datetime]] = {}
        self._lock = RLock()
        self._bypass = bypass
        self._override_key = override_key
        self.add_rules(rules)

    def on_event(self, event: Event, now: datetime, read: Read) -> tuple[Reaction, ...]:
        with self._lock:
            out: list[Reaction] = []
            for rule in self._rules:
                if rule.trigger.fired(event):
                    if rule.delay:
                        out.append(Deferred(hash(rule), rule, now + rule.delay))
                    else:
                        out.append(self._fire(rule, now, read))
                elif rule.delay and isinstance(rule.trigger, Transition) and rule.trigger.channel == event.channel:
                    out.append(Cancel(hash(rule)))
            return tuple(out)

    def on_fire(self, deferred: Deferred, now: datetime, read: Read) -> Report:
        with self._lock:
            return self._fire(deferred.rule, now, read)

    def add_rules(self, rules: Sequence[Rule]) -> None:
        if any(isinstance(rule.trigger, Never) for rule in rules):
            raise ValueError("a NEVER-triggered rule never fires on an event")
        with self._lock:
            self._rules.extend(rules)

    def _fire(self, rule: Rule, now: datetime, read: Read) -> Report:
        key = hash(rule)
        last = self._last_fired.get(key)
        if last is not None and now - last < rule.cooldown:
            return Report(key, Disposition.COOLED, now - last)
        if not self.evaluate([rule], now, read=read, force=True):
            return Report(key, Disposition.BLOCKED)
        self._last_fired[key] = now
        return Report(key, Disposition.FIRED)

    def save_snapshot(self, key: str, payload: Any, deadline: datetime) -> None:
        with self._lock:
            self._snapshots[key] = (payload, deadline)

    def snapshot_active(self, key: str, now: datetime) -> bool:
        with self._lock:
            entry = self._snapshots.get(key)
            return bool(entry and now <= entry[1])

    def read_snapshot(self, key: str, now: datetime) -> Any:
        with self._lock:
            entry = self._snapshots.get(key)
            return entry[0] if entry and now <= entry[1] else None

    def take_snapshot(self, key: str, now: datetime) -> Any:
        with self._lock:
            entry = self._snapshots.pop(key, None)
            return entry[0] if entry and now <= entry[1] else None

    def snapshots(self, now: datetime) -> dict[str, Any]:
        with self._lock:
            return {key: payload for key, (payload, deadline) in self._snapshots.items() if now <= deadline}

    def evaluate[C: Channel](
        self, rules: Iterable[Rule[C]], now: datetime, *, read: Read = _no_read, force: bool
    ) -> tuple[Command[Any, C], ...]:
        with self._lock:
            override_key = self._override_key
            snapshot = self._snapshots.get(override_key) if override_key is not None else None
            active = snapshot is not None and now <= snapshot[1]
            out: list[Command[Any, C]] = []
            for rule in rules:
                for clause in rule.items:
                    if not all(cond.holds(read) for cond in clause.conditions):
                        continue
                    command = clause.command
                    if not force:
                        if command.tag == self._bypass:
                            if override_key is not None and snapshot is not None and active:
                                payload, deadline = snapshot
                                merged = {c.channel: c for c in payload.routine}
                                merged[command.channel] = command
                                self._snapshots[override_key] = (payload._replace(routine=tuple(merged.values())), deadline)
                        elif active:
                            continue
                    out.append(command)
            return tuple(out)

    def override_scene(self, ctx: Any, name: str, commands: tuple[Command[Any], ...], end: datetime, label: str, entry: Any) -> None:
        now = ctx.api.local_now()
        if not self.snapshot_active(name, now):
            self.save_snapshot(name, SnapShot(ctx.api.capture_lights(), end, label), end)
            captured = self.read_snapshot(name, now).routine
            items = ", ".join(f"`{cast(Any, c.channel).one().name}`={c.value}" for c in captured if c.value != ctx.api.m.OFF)
            entry.add(entry.source, ctx.api.Log.SNAPSHOT_TAKEN.format(name=label, end=end, items=items or ctx.api.Log.SNAPSHOT_ALL_OFF))
        ctx.api.dispatch(commands, force=True, entry=entry)

    def restore_scene(self, ctx: Any, name: str, commands: tuple[Command[Any], ...], entry: Any) -> None:
        snapshot = self.take_snapshot(name, ctx.api.local_now())
        if snapshot:
            commands = snapshot.routine
            entry.add(entry.source, ctx.api.Log.SNAPSHOT_RESTORED.format(name=snapshot.label))
        ctx.api.dispatch(commands, force=True, entry=entry)
