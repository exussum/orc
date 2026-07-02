import re
from collections import defaultdict, deque
from collections import namedtuple as nt
from dataclasses import KW_ONLY, dataclass, replace
from datetime import datetime, time
from enum import Enum, auto
from itertools import chain
from types import SimpleNamespace
from typing import TYPE_CHECKING, Tuple

from apscheduler.schedulers.base import BaseScheduler
from mistletoe.block_token import Heading, Table

SnapShot = nt("SnapShot", "routine end")
ThemeOverride = nt("ThemeOverride", "name start end")

if TYPE_CHECKING:
    from orc.api import SnapshotManager
    from orc.view import VersionManager

SUNRISE = "sunrise"
SUNSET = "sunset"

_YOUTUBE_ID_RE = r"^[0-9A-Za-z_-]{11}$"

_STATE_SORT_STOP = -2
_STATE_SORT_INT = -1
_STATE_SORT_ON = 0
_STATE_SORT_OTHER = 1

_CLASS_SORT = {"LGTV": 0, "Light": 1, "Chromecast": 2, "AC": 3}


class Capability(Enum):
    change_level = auto()


class LogSource(str, Enum):
    CALENDAR = "calendar"
    IOT = "iot"
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
    def __init__(self):
        self.entries = deque(maxlen=200)

    def add(self, when, source, action):
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
    def from_cal(cal, type, offset, tz):
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
    rule: "Routine"


@dataclass(frozen=True)
class SensorState:
    name: str
    device_id: str
    connected: bool = False
    state: str | None = None
    battery: int | None = None
    signal: int | None = None
    interval: int | None = None
    online: bool | None = None
    last_change: datetime | None = None


@dataclass
class Config:
    what: object
    state: object
    _: KW_ONLY
    trigger: str | None = None


@dataclass
class SoundState:
    what: object
    content: str | None
    volume: int


@dataclass
class Configs:
    items: Tuple[Config]

    def __init__(self, *items: Config) -> None:
        self.items = tuple(items)


@dataclass
class AdhocConfig(Configs):
    snapshot: "timedelta | None" = None

    def __init__(self, *items: Config, snapshot=None) -> None:
        super().__init__(*items)
        self.snapshot = snapshot


@dataclass
class Routine:
    name: str
    when: str
    items: Tuple[Config]

    def __post_init__(self) -> None:
        if self.when and not isinstance(self.when, time) and ":" in self.when:
            self.when = _str_to_time(self.when)


@dataclass
class Theme:
    name: str
    configs: Tuple[Routine]

    def __init__(self, name: str, *configs: Routine) -> None:
        self.name = name
        self.configs = tuple(configs)


@dataclass
class Secrets:
    access_token: str
    market_holidays_url: str
    ics_url: str
    yolink_id: str
    yolink_secret: str


@dataclass
class AppContext:
    snapshot_manager: "SnapshotManager"
    scheduler: BaseScheduler
    sound_path: str
    version_manager: "VersionManager"


class DeviceEnumMeta(type(Enum)):
    def __sub__(cls, e):
        return set(cls) - e


class DeviceEnum(Enum, metaclass=DeviceEnumMeta):
    def __new__(cls, value, capabilities=frozenset(), room=None):
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


def _fmt(pairs):
    return ", ".join(f"'{v}' in '{t}'" for t, v in pairs)


def build_config(doc, section, light, chromecast, lgtv, required=()):
    config_row = lambda e: SimpleNamespace(type=e[0], expr=e[1], state=e[2])
    sub_tables = [(t, list(map(config_row, rows))) for t, rows in _doc_to_sub_tables(doc, section, 3)]
    if invalid := _validate_states(sub_tables):
        raise ValueError(f"Invalid state values in section '{section}': {_fmt(invalid)}")
    result = {t: Configs(*[_build_config(r.expr, chromecast, light, lgtv, r.state) for r in rows]) for t, rows in sub_tables}
    if missing := set(required) - result.keys():
        raise ValueError(f"Missing required entries in section '{section}': {', '.join(sorted(missing))}")
    return result


def build_ad_hoc_routines(doc, section, light, chromecast, lgtv):
    from datetime import timedelta

    adhoc_row = lambda e: SimpleNamespace(type=e[0], expr=e[1], state=e[2], snapshot=e[3])
    sub_tables = [(t, list(map(adhoc_row, rows))) for t, rows in _doc_to_sub_tables(doc, section, 4, min_columns=3)]
    if invalid := _validate_states(sub_tables):
        raise ValueError(f"Invalid state values in section '{section}': {_fmt(invalid)}")
    if invalid_snapshots := [
        (t, rows[0].snapshot) for t, rows in sub_tables if rows[0].snapshot is not None and not rows[0].snapshot.isdigit()
    ]:
        raise ValueError(f"Invalid snapshot values in section '{section}': {_fmt(invalid_snapshots)}")

    return {
        t: AdhocConfig(
            *[_build_config(r.expr, chromecast, light, lgtv, r.state) for r in rows],
            snapshot=timedelta(hours=int(rows[0].snapshot)) if rows[0].snapshot else None,
        )
        for t, rows in sub_tables
    }


def build_audio_volumes(doc, section, required):
    rows = _doc_to_table(doc, section, 2)

    def _valid(s):
        return s is not None and s.isdigit() and 0 <= int(s) <= 100

    if invalid := [(name, s) for (name, s) in rows if not _valid(s)]:
        raise ValueError(f"Invalid volume values in section '{section}': {_fmt(invalid)}")
    result = {name: int(s) for name, s in rows}
    if missing := set(required) - result.keys():
        raise ValueError(f"Missing required entries in section '{section}': {', '.join(sorted(missing))}")
    return result


def build_enum(doc, section, sub_section, id_lookup=None):
    if sub_section not in ("LGTV", "Light", "Chromecast", "BroadLink", "WebOS", "Leak", "AC"):
        raise ValueError(f"sub_section must be 'LGTV', 'Light', 'Chromecast', 'BroadLink', 'WebOS', 'Leak', or 'AC', got '{sub_section}'")

    sub_table = next((sub_table for (type, sub_table) in _doc_to_sub_tables(doc, section, 4, min_columns=3) if type == sub_section), None)
    if sub_table is None:
        return DeviceEnum(sub_section, {}, module="orc")

    device_row = lambda e: SimpleNamespace(type=e[0], name=e[1], room=e[2], host=e[3])
    rows = list(map(device_row, sub_table))

    for label, attr in (("names", "name"), ("device id", "host")):
        vals = [getattr(r, attr) for r in rows if getattr(r, attr) is not None]
        if duplicates := {v for v in vals if vals.count(v) > 1}:
            raise ValueError(f"Duplicate {label} in '{sub_section}': {duplicates}")

    members, current_room = {}, None
    for i, r in enumerate(rows):
        current_room = r.room or current_room
        if id_lookup is None:
            members[r.name] = (r.host, frozenset(), current_room)
        else:
            members[r.name] = (*id_lookup.get(r.host, (-(i + 1), frozenset())), current_room)
    return DeviceEnum(sub_section, members, module="orc")


def build_highlights(doc, section):
    rows = _doc_to_table(doc, section, 3)

    if invalid := [(name, val) for (name, start, end) in rows for val in (start, end) if _str_to_time(val) is None]:
        raise ValueError(f"Invalid time values in section '{section}': {_fmt(invalid)}")

    return [(name, _str_to_time(start), _str_to_time(end)) for (name, start, end) in rows]


def build_people(doc, section):
    people = defaultdict(set)
    for name, host in _doc_to_table(doc, section, 2):
        people[name].add(host)
    return people


def build_plugins(doc, section):
    from orc import plugins

    result = {t: rows[0][1] for t, rows in _doc_to_sub_tables(doc, section, 2)}

    if missing := [k for k, v in result.items() if not (isinstance(v, str) and hasattr(plugins, v))]:
        raise ValueError(f"Unrecognised plugins in section '{section}': {', '.join(sorted(missing))}")

    return result


def build_themes(doc, routine_section, theme_section, light, chromecast, lgtv, people=None):
    routine_row = lambda e: SimpleNamespace(type=e[0], name=e[1], expr=e[2], state=e[3], trigger=e[4])
    theme_row = lambda e: SimpleNamespace(type=e[0], routine_id=e[1], time=e[2])
    routine_tables = [(t, list(map(routine_row, rows))) for t, rows in _doc_to_sub_tables(doc, routine_section, 5)]
    theme_tables = [(t, list(map(theme_row, rows))) for t, rows in _doc_to_sub_tables(doc, theme_section, 3)]

    _validate_themes(routine_section, theme_section, routine_tables, theme_tables, people)

    if invalid := _validate_states(routine_tables):
        raise ValueError(f"Invalid state values in section '{routine_section}': {_fmt(invalid)}")

    routines = {
        t: Routine(rows[0].name, "", [_build_config(r.expr, chromecast, light, lgtv, r.state, r.trigger) for r in rows])
        for t, rows in routine_tables
    }
    return {t: Theme(t, *[replace(routines[r.routine_id], when=r.time) for r in rows]) for t, rows in theme_tables}


def squish_configs(*configs, state_override=None):
    """
    Take multiple Configs objects, and merge them into one as if they were run sequentially, removing duplicates
    and handling brightness changes.
    """
    rules = defaultdict(list)
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

    rules = list(chain.from_iterable(_squish(e) for e in rules.values()))
    rules.sort(key=_op_cmp)
    return Configs(*rules)


def _validate_themes(routine_section, theme_section, routine_tables, theme_tables, people):
    from orc import Config

    if missing := {Config.THEME_WORK_DAY, Config.THEME_DAY_OFF} - {t for t, _ in theme_tables}:
        raise ValueError(f"Missing required themes in section '{theme_section}': {', '.join(sorted(missing))}")

    for theme_type, rows in theme_tables:
        for r in rows:
            if not _str_to_time(r.time) and r.time not in (SUNRISE, SUNSET):
                raise ValueError(f"Invalid time '{r.time}' in theme '{theme_type}': expected HH:MM, '{SUNRISE}', or '{SUNSET}'")

    known_triggers = set(people or {}) | {Trigger.SYSTEM.value, Trigger.ANYONE.value} | {wc.value for wc in WeatherCondition}

    if invalid_trigger := [
        (t, r.trigger) for t, rows in routine_tables for r in rows if r.trigger not in (None, "") and r.trigger not in known_triggers
    ]:
        raise ValueError(f"Unknown trigger names in section '{routine_section}': {_fmt(invalid_trigger)}")

    if missing := {"Reset"} - {r.name for t, rows in routine_tables for r in rows}:
        raise ValueError(f"Missing required routines in section '{routine_section}': {', '.join(sorted(missing))}")


def _doc_to_sub_tables(doc, section, columns, *, min_columns=None):
    type, result = None, None
    for e in _doc_to_table(doc, section, columns, min_columns=min_columns):
        if e[0] != type and e[0]:
            if result:
                yield type, result
            type, result = e[0], []
        result.append(e)

    if result:
        yield type, result


def _doc_to_table(doc, section, columns, *, min_columns=None):
    # Heading store their contents in a subsequent child element
    # https://github.com/miyuchina/mistletoe/issues/99
    idx = next(
        (i for (i, e) in enumerate(doc.children) if isinstance(e, Heading) and e.children[0].content == section),
        None,
    )
    if idx is None:
        raise ValueError(f"Section '{section}' not found in document")

    markdown_table = next((e for e in doc.children[idx + 1 :] if isinstance(e, Table)), None)
    if markdown_table is None:
        raise ValueError(f"No table found under section '{section}'")

    effective_min = min_columns if min_columns is not None else columns
    rows = list(markdown_table.children)
    if invalid := [(i, len(row.children)) for i, row in enumerate(rows) if not (effective_min <= len(row.children) <= columns)]:
        bad_rows = ", ".join(str(i) for i, _ in invalid)
        raise ValueError(f"Expected {columns} columns in section '{section}', but rows {bad_rows} have the wrong number")

    return tuple(
        tuple(c.children[0].content if c.children else None for c in e.children) + (None,) * (columns - len(e.children)) for e in rows
    )


def _squish(items):
    from orc import Config

    if not items:
        return ()

    last = items[-1]
    if isinstance(last.state, int):
        for e in range(len(items) - 2, -1, -1):
            if items[e].state == Config.STOP:
                return (items[e], last)
        return (last,)

    for e in range(len(items) - 2, -1, -1):
        if isinstance(items[e].state, int):
            return (items[e], last)
    return (last,)


def _build_config(cmd, chromecast, light, lgtv, state, trigger=None):
    if state.isdigit():
        state = int(state)
    return Config(eval(cmd, {"__builtins__": {}}, {"Light": light, "Chromecast": chromecast, "LGTV": lgtv}), state, trigger=trigger or None)


def _op_cmp(k):
    from orc import Config

    class_sort = _CLASS_SORT[k.what.__class__.__name__]

    if k.state == Config.STOP:
        sub_sort = _STATE_SORT_STOP
    elif isinstance(k.state, int):
        sub_sort = _STATE_SORT_INT
    elif k.state == Config.ON:
        sub_sort = _STATE_SORT_ON
    else:
        sub_sort = _STATE_SORT_OTHER
    return (class_sort, sub_sort)


def _str_to_time(x):
    parts = x.split(":") if x else []
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour, minute)


def _valid_state(e):
    from orc import Config

    return e in (Config.ON, Config.OFF, Config.STOP) or e.isdigit() or re.match(_YOUTUBE_ID_RE, e)


def _validate_states(sub_tables):
    return [(t, r.state) for t, rows in sub_tables for r in rows if not _valid_state(r.state)]
