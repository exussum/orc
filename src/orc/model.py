import importlib
import re
from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import KW_ONLY, dataclass, field, replace
from datetime import date, datetime, time, timedelta
from enum import Enum, EnumType, auto
from itertools import chain
from typing import TYPE_CHECKING, Any, NamedTuple, Self

from apscheduler.schedulers.base import BaseScheduler

from orc.collections import doc_to_sub_tables, doc_to_table, parse_kv
from orc.security import safe_eval

if TYPE_CHECKING:
    from orc.api import SnapshotManager
    from orc.view import VersionManager


class SnapShot(NamedTuple):
    routine: Configs
    end: datetime


class ThemeOverride(NamedTuple):
    name: str
    start: date
    end: date


SUNRISE = "sunrise"
SUNSET = "sunset"

OFF = "off"
ON = "on"
STOP = "stop"
RESUME = "resume"
PAUSE = "pause"
FOLLOW = "follow"
THEME_WORK_DAY = "work day"
THEME_DAY_OFF = "day off"
AUDIO_INFO = "INFO"
AUDIO_FATAL = "FATAL"

_YOUTUBE_ID_RE = r"^[0-9A-Za-z_-]{11}$"

_ERR_STATE = "Invalid state {!r}: expected one of 'on', 'off', 'stop', 'pause', 'resume', an integer, or an 11-character YouTube ID"
_ERR_PARAMS = "Invalid parameter {}={!r}"
_ERR_TIME = "Invalid time {!r}: expected HH:MM, 'sunrise', or 'sunset'"
# "device" plugins are invoked per-device from the /device grid (via /api/run?device=…);
# they render no button and are not auto-invoked, unlike the other sections.
_VALID_SECTIONS = frozenset({"scene", "system", "hubitat", "device"})
_ERR_PLUGIN = "Cannot load plugin {!r}: {}. Expected a fully qualified callable like 'orc.plugins.my_plugin'. Ensure the module exists and the function is defined within it."

_STATE_SORT_STOP = -2
_STATE_SORT_INT = -1
_STATE_SORT_ON = 0
_STATE_SORT_OTHER = 1

_CLASS_SORT = {"Light": 1, "Chromecast": 2, "AC": 3}


class Capability(Enum):
    change_level = auto()


class LogSource(str, Enum):
    CALENDAR = "calendar"
    ROUTINE = "routine"
    REMOTE = "remote"
    MANUAL = "manual"
    SYSTEM = "system"


class Trigger(str, Enum):
    SYSTEM = "SYSTEM"
    ANYONE = "ANYONE"


class WeatherCondition(str, Enum):
    SUNNY = "SUNNY"
    CLOUDY = "CLOUDY"


@dataclass
class LogEntry:
    timestamp: datetime
    source: LogSource
    action: str


class ActivityLog:
    def __init__(self) -> None:
        self.entries: deque[LogEntry] = deque(maxlen=200)

    def add(self, when: datetime, source: LogSource, action: str) -> None:
        self.entries.appendleft(LogEntry(when, source, action))


@dataclass
class CalendarEvent:
    WARNING = "warning"
    ALARM = "alarm"

    uuid: str
    summary: str
    datetime: datetime
    type: str

    @staticmethod
    def from_cal(cal: Any, type: str, offset: timedelta, tz: Any) -> CalendarEvent:
        return CalendarEvent(
            cal.uid.to_ical().decode() + " " + type,
            cal.summary.to_ical().decode("utf-8"),
            cal.start.astimezone(tz) + offset,
            type,
        )


@dataclass
class CalendarJob:
    event_type: str
    summary: str


@dataclass
class IotJob:
    rule: Routine


@dataclass
class Config:
    what: DeviceEnum | type[DeviceEnum] | set[DeviceEnum]
    state: str | int
    _: KW_ONLY
    trigger: str | None = None


@dataclass
class SoundState:
    what: DeviceEnum
    content: str | None
    volume: int


@dataclass
class Configs[T = Config]:
    items: tuple[T, ...]

    def __init__(self, *items: T) -> None:
        self.items = tuple(items)


@dataclass
class Plugin:
    func: Callable[..., object]
    delay: timedelta = field(default_factory=timedelta)
    section: str = "scene"
    icon: str = "rocket-launch"


@dataclass
class AdhocConfig(Configs):
    snapshot: timedelta | None = None
    delay: timedelta = field(default_factory=timedelta)
    section: str = "scene"
    reset: bool = True

    def __init__(
        self, *items: Config, snapshot: timedelta | None = None, delay: timedelta = timedelta(), section: str = "scene", reset: bool = True
    ) -> None:
        if snapshot and delay:
            raise ValueError("snapshot and delay cannot both be set")
        if snapshot and not reset:
            raise ValueError("snapshot and reset=false cannot both be set")
        super().__init__(*items)
        self.snapshot = snapshot
        self.delay = delay
        self.section = section
        self.reset = reset


@dataclass
class Routine:
    name: str
    when: str
    items: Sequence[Config]

    def __post_init__(self) -> None:
        if self.when and not isinstance(self.when, time) and ":" in self.when:
            self.when = column_to_value("time", self.when)


@dataclass
class Theme:
    name: str
    configs: tuple[Routine, ...]

    def __init__(self, name: str, *configs: Routine) -> None:
        self.name = name
        self.configs = tuple(configs)


@dataclass
class Secrets:
    access_token: str
    market_holidays_url: str
    ics_url: str
    # Every secret fetched from the store, so plugins can read their own keys
    # (e.g. secrets.get("SOME_TOKEN")) without core declaring a typed field for each.
    _raw: dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> str:
        return self._raw.get(key, "")


@dataclass
class AppContext:
    snapshot_manager: SnapshotManager
    scheduler: BaseScheduler
    sound_path: str
    version_manager: VersionManager


class DeviceEnumMeta(EnumType):
    def __sub__(cls, e: set[Any]) -> set[Any]:
        return set(cls) - e


class DeviceEnum(Enum, metaclass=DeviceEnumMeta):
    capabilities: frozenset[Capability]
    room: str | None

    def __new__(cls, value: Any, capabilities: frozenset[Capability] = frozenset(), room: str | None = None) -> Self:
        obj = object.__new__(cls)
        if isinstance(value, tuple):
            obj._value_ = value[0]
            obj.capabilities = value[1] if len(value) > 1 else frozenset()
            obj.room = value[2] if len(value) > 2 else None
        else:
            obj._value_ = value
            obj.capabilities = capabilities
            obj.room = room
        return obj


@dataclass(frozen=True)
class DeviceType:
    """A registered device type with everything plugins declared about it, so
    consumers iterate whole devices rather than parallel per-attribute maps.
    ``cls`` is the runtime-built enum class, so callers reach members via
    ``cls[name]``."""

    cls: type[DeviceEnum]
    icon: str
    controllable: bool
    reset_excluded: bool
    dispatch: Callable[..., None] | None


@dataclass(frozen=True)
class Registry:
    """Immutable snapshot of what plugins registered, built once per config load and
    exposed as ``orc.config.registry``.

    ``click_hooks`` and ``button_labels`` are keyed by button/action id, not device
    type, so they sit alongside ``devices`` rather than folding into a DeviceType.
    ``state_providers`` are stored as functions and called fresh by consumers on each
    request, so the returned rows reflect live device state."""

    devices: dict[str, DeviceType]
    click_hooks: dict[str, str]
    button_labels: dict[str, str]
    state_providers: dict[str, Callable[[], Any]]
    startup_hooks: list[Callable[[], None]]


def build_ad_hoc_routines(doc: Any, section: str) -> dict[Any, AdhocConfig]:
    return {
        t: AdhocConfig(
            *[Config(r.device, r.state) for r in rows],
            **rows[0].parameters,
        )
        for t, rows in _typed_sub_tables(doc, section, ("Type", "Device", "State", "Parameters"))
    }


def build_audio_volumes(doc: Any, section: str, required: Iterable[str]) -> dict[Any, int]:
    rows = doc_to_table(doc, section, 2)

    def _valid(s: str | None) -> bool:
        return s is not None and s.isdigit() and 0 <= int(s) <= 100

    if invalid := [(name, s) for (name, s) in rows if not _valid(s)]:
        raise ValueError(f"Invalid volume values in section '{section}': {_fmt(invalid)}")
    result = {name: int(s) for name, s in rows}
    if missing := set(required) - result.keys():
        raise ValueError(f"Missing required entries in section '{section}': {', '.join(sorted(missing))}")
    return result


def build_config(doc: Any, section: str, required: Iterable[str] = ()) -> dict[Any, Configs]:
    result = {
        t: Configs(*[Config(r.device, r.state) for r in rows]) for t, rows in _typed_sub_tables(doc, section, ("Type", "Device", "State"))
    }
    if missing := set(required) - result.keys():
        raise ValueError(f"Missing required entries in section '{section}': {', '.join(sorted(missing))}")
    return result


def build_enum(
    doc: Any, section: str, sub_section: str, id_lookup: dict[Any, tuple[Any, ...]] | None = None, *, device_types: Iterable[str]
) -> type[DeviceEnum]:
    if sub_section not in device_types:
        raise ValueError(f"sub_section must be one of {list(device_types)}, got '{sub_section}'")

    rows = next((rows for (type, rows) in _typed_sub_tables(doc, section, ("Type", "Name", "Room", "Host")) if type == sub_section), None)
    if rows is None:
        # functional Enum API: mypy checks against the member-level __new__ rather than EnumMeta.__call__
        return DeviceEnum(sub_section, {}, module="orc")  # type: ignore[call-arg,arg-type,return-value]

    for label, attr in (("names", "name"), ("device id", "host")):
        vals = [getattr(r, attr) for r in rows if getattr(r, attr) is not None]
        if duplicates := {v for v in vals if vals.count(v) > 1}:
            raise ValueError(f"Duplicate {label} in '{sub_section}': {duplicates}")

    members: dict[Any, tuple[Any, ...]] = {}
    current_room: Any = None
    for i, r in enumerate(rows):
        current_room = r.room or current_room
        if id_lookup is None:
            members[r.name] = (r.host, frozenset(), current_room)
        else:
            members[r.name] = (*id_lookup.get(r.host, (-(i + 1), frozenset())), current_room)
    # functional Enum API: mypy checks against the member-level __new__ rather than EnumMeta.__call__
    return DeviceEnum(sub_section, members, module="orc")  # type: ignore[call-arg,arg-type,return-value]


def build_highlights(doc: Any, section: str) -> list[tuple[Any, Any, Any]]:
    return [(name, column_to_value("time", start), column_to_value("time", end)) for (name, start, end) in doc_to_table(doc, section, 3)]


def build_people(doc: Any, section: str) -> defaultdict[Any, set[Any]]:
    people: defaultdict[Any, set[Any]] = defaultdict(set)
    for name, host, mac in doc_to_table(doc, section, 3):
        if not mac:
            raise ValueError(f"Person '{name}' host '{host}' is missing a MAC address")
        people[name].add((host, mac))
    return people


def build_plugins(doc: Any, section: str) -> dict[Any, Plugin]:
    return {
        t: Plugin(func=rows[0].plugin, **rows[0].parameters)
        for t, rows in doc_to_sub_tables(doc, section, ("Name", "Plugin", "Parameters"), cast=column_to_value)
    }


def build_themes(doc: Any, routine_section: str, theme_section: str, people: Iterable[str] | None = None) -> dict[Any, Theme]:
    routine_tables = [(t, rows) for t, rows in _typed_sub_tables(doc, routine_section, ("Type", "Name", "Device", "State", "Trigger"))]
    theme_tables = [(t, rows) for t, rows in _typed_sub_tables(doc, theme_section, ("Type", "Routine", "Time"))]

    _validate_themes(routine_section, theme_section, routine_tables, theme_tables, people)

    routines = {
        t: Routine(rows[0].name, "", [Config(r.device, r.state, trigger=r.trigger or None) for r in rows]) for t, rows in routine_tables
    }
    return {t: Theme(t, *[replace(routines[r.routine], when=r.time) for r in rows]) for t, rows in theme_tables}


def column_to_value(col: str, val: Any) -> Any:
    import orc

    if col.lower() == "value":
        return int(val) if val and val.isdigit() else val
    elif col.lower() == "device":
        # device enums are populated on the orc package at runtime by Config.load; build
        # the eval namespace keyed by class name (== the device-type name).
        return safe_eval(val, {e.__name__: e for e in orc.device_enums})
    elif col.lower() == "state":
        if val and val.isdigit():
            return int(val)
        if val in (ON, OFF, STOP, PAUSE, RESUME) or (val and re.match(_YOUTUBE_ID_RE, val)):
            return val
        raise ValueError(_ERR_STATE.format(val))
    elif col.lower() in ("delay", "snapshot"):
        if not val:
            return timedelta()
        elif val.isdigit():
            return timedelta(minutes=int(val))
        else:
            raise ValueError(_ERR_PARAMS.format(col, val))
    elif col.lower() == "section":
        if val in _VALID_SECTIONS:
            return val
        raise ValueError(_ERR_PARAMS.format(col, val))
    elif col.lower() == "reset":
        if val in ("true", "false"):
            return val == "true"
        raise ValueError(_ERR_PARAMS.format(col, val))
    elif col.lower() == "parameters":
        parsed = {k: column_to_value(k, v) for k, v in parse_kv(val).items()}
        return {"section": "scene", "delay": timedelta(), **parsed}
    elif col.lower() in ("start", "stop"):  # blank on continuation rows: a group's window lives on its first row
        return val and column_to_value("time", val)
    elif col.lower() == "time":
        if val in (SUNRISE, SUNSET):
            return val
        parts = val.split(":") if val else []
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            raise ValueError(_ERR_TIME.format(val))
        hour, minute = int(parts[0]), int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(_ERR_TIME.format(val))
        return time(hour, minute)
    elif col.lower() == "plugin":
        try:
            module_path, fn_name = val.rsplit(".", 1)
            return getattr(importlib.import_module(module_path), fn_name)  # nosemgrep: non-literal-import
        except Exception as exc:
            raise ValueError(_ERR_PLUGIN.format(val, exc)) from exc
    return val


def squish_configs(*configs: Configs | Routine, state_override: Any = None) -> Configs:
    """
    Take multiple Configs objects, and merge them into one as if they were run sequentially, removing duplicates
    and handling brightness changes.
    """
    rules: defaultdict[Any, list[Config]] = defaultdict(list)
    for routine in configs:
        for rule in routine.items:

            what = [rule.what] if isinstance(rule.what, Enum) else rule.what
            for e in what:
                rules[e].append(
                    Config(
                        what=e,
                        state=rule.state if state_override is None else state_override,
                        trigger=rule.trigger,
                    )
                )

    flattened = list(chain.from_iterable(_squish(e) for e in rules.values()))
    flattened.sort(key=_op_cmp)
    return Configs(*flattened)


def _fmt(pairs: Iterable[tuple[Any, Any]]) -> str:
    return ", ".join(f"'{v}' in '{t}'" for t, v in pairs)


def _op_cmp(k: Config) -> tuple[int, int]:
    # plugin-owned device types aren't in _CLASS_SORT; they default past the core types
    class_sort = _CLASS_SORT.get(k.what.__class__.__name__, 10)

    if k.state == STOP:
        sub_sort = _STATE_SORT_STOP
    elif isinstance(k.state, int):
        sub_sort = _STATE_SORT_INT
    elif k.state == ON:
        sub_sort = _STATE_SORT_ON
    else:
        sub_sort = _STATE_SORT_OTHER
    return (class_sort, sub_sort)


def _squish(items: list[Config]) -> tuple[Config, ...]:
    if not items:
        return ()

    last = items[-1]
    if isinstance(last.state, int):
        for e in range(len(items) - 2, -1, -1):
            if items[e].state == STOP:
                return (items[e], last)
        return (last,)

    for e in range(len(items) - 2, -1, -1):
        if isinstance(items[e].state, int):
            return (items[e], last)
    return (last,)


def _typed_sub_tables(doc: Any, section: str, columns: tuple[str, ...]) -> Iterator[tuple[Any, list[Any]]]:
    return doc_to_sub_tables(doc, section, columns, cast=column_to_value)


def _validate_themes(
    routine_section: str,
    theme_section: str,
    routine_tables: list[tuple[Any, list[Any]]],
    theme_tables: list[tuple[Any, list[Any]]],
    people: Iterable[str] | None,
) -> None:
    if missing := {THEME_WORK_DAY, THEME_DAY_OFF} - {t for t, _ in theme_tables}:
        raise ValueError(f"Missing required themes in section '{theme_section}': {', '.join(sorted(missing))}")

    known_triggers = set(people or {}) | {Trigger.SYSTEM.value, Trigger.ANYONE.value} | {wc.value for wc in WeatherCondition}

    if invalid_trigger := [
        (t, r.trigger) for t, rows in routine_tables for r in rows if r.trigger not in (None, "") and r.trigger not in known_triggers
    ]:
        raise ValueError(f"Unknown trigger names in section '{routine_section}': {_fmt(invalid_trigger)}")

    if missing := {"Reset"} - {r.name for t, rows in routine_tables for r in rows}:
        raise ValueError(f"Missing required routines in section '{routine_section}': {', '.join(sorted(missing))}")
