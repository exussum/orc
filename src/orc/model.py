import importlib
import re
from collections import defaultdict, deque
from collections.abc import Callable, Sequence
from dataclasses import KW_ONLY, dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum, EnumType, auto
from itertools import chain
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, NamedTuple, Self

from apscheduler.schedulers.base import BaseScheduler

from orc.security import safe_eval

if TYPE_CHECKING:
    from orc import Config as OrcConfig
    from orc.api import SnapshotManager
    from orc.view import VersionManager


class SnapShot(NamedTuple):
    routine: Configs
    end: datetime
    label: str = ""


class Person(NamedTuple):
    host: str
    mac: str


type Listener = Callable[[DeviceState, str, Any, Any], None]
type ButtonListener = Callable[[int, int, str], None]


class Volume(NamedTuple):
    INFO: int
    FATAL: int


class ThemeOverride(NamedTuple):
    name: str
    start: date
    end: date


class RetryStats(NamedTuple):
    id: int
    failed: int
    clean: int
    retried: int


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
_VALID_SECTIONS = frozenset({"scene", "system", "device"})
_ERR_FUNCTION = (
    "Cannot load function {!r}: {}. Expected a fully qualified callable like 'orc.plugins.my_plugin'. "
    "Ensure the module exists and the function is defined within it."
)

_ERR_MODULE = "Cannot load module {!r}: {}. Expected an importable module like 'orc.dal.mqtt.stub'."

_STATE_SORT_STOP = -2
_STATE_SORT_INT = -1
_STATE_SORT_ON = 0
_STATE_SORT_OTHER = 1

_CLASS_SORT = {"Light": 0, "Chromecast": 1, "AC": 2}


class Capability(Enum):
    change_level = auto()


class LogSource(str, Enum):
    CALENDAR = "calendar"
    ROUTINE = "routine"
    MANUAL = "manual"
    SYSTEM = "system"
    PLUGIN = "plugin"
    EXTERNAL = "external"


class Trigger(str, Enum):
    SYSTEM = "SYSTEM"
    ANYONE = "ANYONE"


class WeatherCondition(str, Enum):
    SUNNY = "SUNNY"
    CLOUDY = "CLOUDY"


@dataclass(frozen=True)
class DeviceState:
    """Last-received device document from the hub's MQTT export."""

    id: int
    name: str
    attributes: dict[str, Any]
    last_activity: str | None


class BatteryLevel(str, Enum):
    CRITICAL = "CRITICAL"
    LOW = "LOW"
    MID = "MID"
    HIGH = "HIGH"

    @property
    def is_critical(self) -> bool:
        return self is BatteryLevel.CRITICAL

    @classmethod
    def from_fraction(cls, value: Any, out_of: int) -> BatteryLevel:
        pct = int(value) * 100 // out_of
        if pct <= 10:
            return cls.CRITICAL
        elif pct <= 25:
            return cls.LOW
        elif pct <= 75:
            return cls.MID
        else:
            return cls.HIGH


@dataclass
class LogEntry:
    timestamp: datetime
    source: LogSource
    action: str
    children: list[LogEntry] = field(default_factory=list)

    def add(self, source: LogSource, action: str) -> LogEntry:
        entry = LogEntry(datetime.now(self.timestamp.tzinfo), source, action)
        self.children.append(entry)
        return entry


class ActivityLog:
    def __init__(self) -> None:
        self.entries: deque[LogEntry] = deque(maxlen=200)

    def add(self, when: datetime, source: LogSource, action: str) -> LogEntry:
        entry = LogEntry(when, source, action)
        self.entries.appendleft(entry)
        return entry


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
    name: str
    func: Callable[..., object]
    module: ModuleType | None = None
    delay: timedelta = field(default_factory=timedelta)
    section: str = "scene"
    icon: str = "rocket-launch"
    backend: ModuleType | None = None


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
    when: str | time
    items: Sequence[Config]
    skip_replay: bool = False


@dataclass
class Theme:
    name: str
    configs: tuple[Routine, ...]

    def __init__(self, name: str, *configs: Routine) -> None:
        self.name = name
        self.configs = tuple(configs)


@dataclass
class Secrets:
    hubitat_access_token: str = ""
    market_holidays_url: str = ""
    ics_url: str = ""
    mqtt_user: str = ""
    mqtt_password: str = ""
    # Plugin-consumed secrets; core never reads these. A key with an in-repo
    # consumer belongs on a typed field instead.
    other: dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> str:
        return self.other.get(key) or ""


@dataclass
class AppContext:
    """Everything a plugin may touch, so plugins never import orc internals directly.
    The module fields are static; they default to lazy imports because this module
    can't import orc.api at import time (api imports model)."""

    snapshot_manager: SnapshotManager
    scheduler: BaseScheduler
    sound_path: str
    version_manager: VersionManager
    config: OrcConfig = field(default_factory=lambda: importlib.import_module("orc").config)
    api: ModuleType = field(default_factory=lambda: importlib.import_module("orc.api"))
    model: ModuleType = field(default_factory=lambda: importlib.import_module("orc.model"))
    orc: ModuleType = field(default_factory=lambda: importlib.import_module("orc"))


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
    dispatch: Callable[..., None] | None

    def handles(self, name: str) -> bool:
        return name in self.cls.__members__


@dataclass
class Registry:
    """What plugins registered, built per config load and exposed as
    ``orc.config.registry``.

    ``scripts`` maps served filename to the plugin's JS file on disk; all enabled
    plugins' files are served in the ``/hooks.js`` bundle and register themselves
    with the browser hooks. ``button_labels`` are keyed by button/action id, not device
    type, so they sit alongside ``devices`` rather than folding into a DeviceType.
    ``state_providers`` are registered by setup hooks (``api.add_state_provider``)
    and called fresh by consumers on each request, so the returned rows reflect live
    device state."""

    devices: dict[str, DeviceType]
    scripts: dict[str, Path]
    button_labels: dict[str, str]
    state_providers: dict[str, Callable[[], Any]]
    setup_hooks: list[Callable[[AppContext], None]]
    ctx: AppContext | None = None  # set once at startup; None during config load


def column_to_value(col: str, val: Any) -> Any:
    import orc

    if col.lower() == "value":
        return int(val) if val and val.isdigit() else val
    elif col.lower() == "device":
        # device enums are populated on the orc package at runtime by Config.load; build
        # the eval namespace keyed by class name (== the device-type name).
        return safe_eval(val, {e.__name__: e for e in orc.device_enums})
    elif col.lower() == "state":
        if val in (ON, OFF, STOP, PAUSE, RESUME) or (val and re.match(_YOUTUBE_ID_RE, val)):
            return val
        if val and val.isdigit():
            return int(val)
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
    elif col.lower() in ("start", "stop", "time"):
        if val in (SUNRISE, SUNSET):
            return val
        parts = val.split(":") if val else []
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            raise ValueError(_ERR_TIME.format(val))
        hour, minute = int(parts[0]), int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(_ERR_TIME.format(val))
        return time(hour, minute)
    elif col.lower() == "module":
        try:
            return importlib.import_module(val)  # nosemgrep: non-literal-import
        except Exception as exc:
            raise ValueError(_ERR_MODULE.format(val, exc)) from exc
    elif col.lower() == "function":
        try:
            module_path, fn_name = val.rsplit(".", 1)
            return getattr(importlib.import_module(module_path), fn_name)  # nosemgrep: non-literal-import
        except Exception as exc:
            raise ValueError(_ERR_FUNCTION.format(val, exc)) from exc
    return val


def squish_configs(
    *configs: Configs | Routine, state_override: Any = None, on_conflict: Callable[[Any, list[Any]], object] = lambda what, states: None
) -> Configs:
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

    for what, items in rules.items():
        if {c.state for c in items} - {c.state for c in _squish(items)}:
            on_conflict(what, [c.state for c in items])

    flattened = list(chain.from_iterable(_squish(e) for e in rules.values()))
    flattened.sort(key=_op_cmp)
    return Configs(*flattened)


def _op_cmp(k: Config) -> tuple[int, int]:
    # types never declared controllable tie past everything registered
    class_sort = _CLASS_SORT.get(k.what.__class__.__name__, len(_CLASS_SORT))

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
