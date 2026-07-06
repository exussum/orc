import importlib
import re
from collections import defaultdict, deque
from collections import namedtuple as nt
from dataclasses import KW_ONLY, dataclass, field, replace
from datetime import datetime, time, timedelta
from enum import Enum, auto
from itertools import chain
from typing import TYPE_CHECKING, Tuple

from apscheduler.schedulers.base import BaseScheduler

from orc.collections import doc_to_sub_tables, doc_to_table, parse_kv
from orc.security import safe_eval, safe_import

SnapShot = nt("SnapShot", "routine end")
ThemeOverride = nt("ThemeOverride", "name start end")

if TYPE_CHECKING:
    from orc.api import SnapshotManager
    from orc.view import VersionManager

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
_VALID_SECTIONS = frozenset({"scene", "system"})
_ERR_PLUGIN = "Cannot load plugin {!r}: {}. Expected a fully qualified callable like 'orc.plugins.my_plugin'. Ensure the module exists and the function is defined within it."

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
class Plugin:
    func: object
    delay: timedelta = field(default_factory=timedelta)
    section: str = "scene"
    icon: str = "rocket-launch"


@dataclass
class AdhocConfig(Configs):
    snapshot: "timedelta | None" = None
    delay: timedelta = field(default_factory=timedelta)
    section: str = "scene"

    def __init__(self, *items: Config, snapshot=None, delay=timedelta(), section="scene") -> None:
        if snapshot and delay:
            raise ValueError("snapshot and delay cannot both be set")
        super().__init__(*items)
        self.snapshot = snapshot
        self.delay = delay
        self.section = section


@dataclass
class Routine:
    name: str
    when: str
    items: Tuple[Config]

    def __post_init__(self) -> None:
        if self.when and not isinstance(self.when, time) and ":" in self.when:
            self.when = column_to_value("time", self.when)


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


def build_ad_hoc_routines(doc, section):
    return {
        t: AdhocConfig(
            *[Config(r.device, r.state) for r in rows],
            **rows[0].parameters,
        )
        for t, rows in _typed_sub_tables(doc, section, ("Type", "Device", "State", "Parameters"))
    }


def build_audio_volumes(doc, section, required):
    rows = doc_to_table(doc, section, 2)

    def _valid(s):
        return s is not None and s.isdigit() and 0 <= int(s) <= 100

    if invalid := [(name, s) for (name, s) in rows if not _valid(s)]:
        raise ValueError(f"Invalid volume values in section '{section}': {_fmt(invalid)}")
    result = {name: int(s) for name, s in rows}
    if missing := set(required) - result.keys():
        raise ValueError(f"Missing required entries in section '{section}': {', '.join(sorted(missing))}")
    return result


def build_config(doc, section, required=()):
    result = {
        t: Configs(*[Config(r.device, r.state) for r in rows]) for t, rows in _typed_sub_tables(doc, section, ("Type", "Device", "State"))
    }
    if missing := set(required) - result.keys():
        raise ValueError(f"Missing required entries in section '{section}': {', '.join(sorted(missing))}")
    return result


def build_enum(doc, section, sub_section, id_lookup=None):
    if sub_section not in ("LGTV", "Light", "Chromecast", "BroadLink", "WebOS", "Leak", "AC"):
        raise ValueError(f"sub_section must be 'LGTV', 'Light', 'Chromecast', 'BroadLink', 'WebOS', 'Leak', or 'AC', got '{sub_section}'")

    rows = next((rows for (type, rows) in _typed_sub_tables(doc, section, ("Type", "Name", "Room", "Host")) if type == sub_section), None)
    if rows is None:
        return DeviceEnum(sub_section, {}, module="orc")

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
    return [(name, column_to_value("time", start), column_to_value("time", end)) for (name, start, end) in doc_to_table(doc, section, 3)]


def build_people(doc, section):
    people = defaultdict(set)
    for name, host in doc_to_table(doc, section, 2):
        people[name].add(host)
    return people


def build_plugins(doc, section):
    return {
        t: Plugin(func=rows[0].plugin, **rows[0].parameters)
        for t, rows in doc_to_sub_tables(doc, section, ("Name", "Plugin", "Parameters"), cast=column_to_value)
    }


def build_themes(doc, routine_section, theme_section, people=None):
    routine_tables = [(t, rows) for t, rows in _typed_sub_tables(doc, routine_section, ("Type", "Name", "Device", "State", "Trigger"))]
    theme_tables = [(t, rows) for t, rows in _typed_sub_tables(doc, theme_section, ("Type", "Routine", "Time"))]

    _validate_themes(routine_section, theme_section, routine_tables, theme_tables, people)

    routines = {
        t: Routine(rows[0].name, "", [Config(r.device, r.state, trigger=r.trigger or None) for r in rows]) for t, rows in routine_tables
    }
    return {t: Theme(t, *[replace(routines[r.routine], when=r.time) for r in rows]) for t, rows in theme_tables}


def column_to_value(col, val):
    import orc

    if col.lower() == "value":
        return int(val) if val and val.isdigit() else val
    elif col.lower() == "device":
        _ns = {cls.__name__: cls for cls in (orc.Light, orc.Chromecast, orc.BroadLink, orc.WebOS, orc.Leak, orc.AC, orc.LGTV)}
        return safe_eval(val, _ns)
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
    elif col.lower() == "parameters":
        parsed = {k: column_to_value(k, v) for k, v in parse_kv(val).items()}
        return {"section": "scene", "delay": timedelta(), **parsed}
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
            return safe_import(val)
        except Exception as exc:
            raise ValueError(_ERR_PLUGIN.format(val, exc)) from exc
    return val


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


def _fmt(pairs):
    return ", ".join(f"'{v}' in '{t}'" for t, v in pairs)


def _op_cmp(k):
    class_sort = _CLASS_SORT[k.what.__class__.__name__]

    if k.state == STOP:
        sub_sort = _STATE_SORT_STOP
    elif isinstance(k.state, int):
        sub_sort = _STATE_SORT_INT
    elif k.state == ON:
        sub_sort = _STATE_SORT_ON
    else:
        sub_sort = _STATE_SORT_OTHER
    return (class_sort, sub_sort)


def _squish(items):
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


def _typed_sub_tables(doc, section, columns):
    return doc_to_sub_tables(doc, section, columns, cast=column_to_value)


def _validate_themes(routine_section, theme_section, routine_tables, theme_tables, people):
    if missing := {THEME_WORK_DAY, THEME_DAY_OFF} - {t for t, _ in theme_tables}:
        raise ValueError(f"Missing required themes in section '{theme_section}': {', '.join(sorted(missing))}")

    known_triggers = set(people or {}) | {Trigger.SYSTEM.value, Trigger.ANYONE.value} | {wc.value for wc in WeatherCondition}

    if invalid_trigger := [
        (t, r.trigger) for t, rows in routine_tables for r in rows if r.trigger not in (None, "") and r.trigger not in known_triggers
    ]:
        raise ValueError(f"Unknown trigger names in section '{routine_section}': {_fmt(invalid_trigger)}")

    if missing := {"Reset"} - {r.name for t, rows in routine_tables for r in rows}:
        raise ValueError(f"Missing required routines in section '{routine_section}': {', '.join(sorted(missing))}")
