import importlib
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import KW_ONLY, dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum, EnumType, StrEnum, auto
from itertools import chain
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple, Self
from zoneinfo import ZoneInfo

from apscheduler.schedulers.base import BaseScheduler

if TYPE_CHECKING:
    from flask import Blueprint

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


class ThemeOverride(NamedTuple):
    name: str
    start: date
    end: date


class Settings(NamedTuple):
    """Core settings from ``setting`` config lines. None marks a required key
    (enforced by loader.validate); the rest default here when the line is omitted."""

    base_url: str | None = None
    lan_domain: str | None = None
    jobs_db: str | None = None
    lat: float | None = None
    long: float | None = None
    broadlink_codes: str | None = None
    mqtt_host: str | None = None
    warning_device: "DeviceEnum | None" = None
    attention_device: "DeviceEnum | None" = None
    emergency_device: "DeviceEnum | None" = None
    tz: ZoneInfo = ZoneInfo("America/New_York")
    hubitat_url: str = "http://hubitat.example"
    http_timeout: int = 5
    port: int = 8000
    emergency_routine: str | None = None

    @classmethod
    def build(cls, **values: Any) -> Settings:
        if "tz" in values:
            values["tz"] = ZoneInfo(values["tz"])
        return cls(**values)


class RetryStats(NamedTuple):
    id: int
    failed: int
    clean: int
    retried: int


class DeviceStatus(NamedTuple):
    name: str
    details: dict[str, Any]
    label: str | None = None
    action: str | None = None


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

_ERR_TIME = "Invalid time {!r}: expected HH:MM, 'sunrise', or 'sunset'"

_STATE_SORT_STOP = -2
_STATE_SORT_INT = -1
_STATE_SORT_ON = 0
_STATE_SORT_OTHER = 1

_CLASS_SORT = {"Light": 0, "Chromecast": 1, "AC": 2}


class Capability(Enum):
    change_level = auto()


_LOG_SOURCES: dict[str, int] = {}


class LogSourceEnum(StrEnum):
    def __init__(self, value: str) -> None:
        if value in _LOG_SOURCES:
            raise ValueError(f"Log source {value!r} is already registered")
        _LOG_SOURCES[value] = len(_LOG_SOURCES)

    @property
    def badge_color(self) -> str:
        # Qualitative-palette construction (hue-spaced, constant chroma):
        # https://colorspace.r-forge.r-project.org/articles/hcl_palettes.html
        # with two deviations: hues follow the golden angle instead of 360/n, so
        # a new source lands in the largest remaining hue gap and never recolors
        # earlier ones; and lightness alternates between two bands rather than
        # staying constant, because colorblind vision flattens hue — the
        # lightness step is what keeps adjacent badges apart.
        i = _LOG_SOURCES[self]
        return f"oklch({0.585 if i % 2 else 0.485} 0.10 {i * 137.5 % 360})"


class LogSource(LogSourceEnum):
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


class Alarm(str, Enum):
    WARNING = "WARNING"
    ATTENTION = "ATTENTION"
    EMERGENCY = "EMERGENCY"


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
    source: LogSourceEnum
    action: str
    children: list[LogEntry] = field(default_factory=list)

    def add(self, source: LogSourceEnum, action: str) -> LogEntry:
        entry = LogEntry(datetime.now(self.timestamp.tzinfo), source, action)
        self.children.append(entry)
        return entry


@dataclass
class IotJob:
    rule: Routine


class Speak(str):
    """Config.state value meaning 'speak this text aloud' — distinct from a plain str
    (a file path or stream URL) and an int (volume). Log messages use backticks for
    markdown emphasis in the log view; strip them here since TTS shouldn't say them."""

    def __new__(cls, text: str) -> "Speak":
        return super().__new__(cls, text.replace("`", ""))


class MediaUrl(str):
    content_type: ClassVar[str]


class AlertVideo(MediaUrl):
    content_type = "video/mp4"


class Stream(MediaUrl):
    content_type = "audio/mp3"


class YouTubeId(str):
    pass


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
    module: ModuleType
    section: str | None = None  # a section is what renders a button; None = no button
    icon: str = "rocket-launch"
    backend: ModuleType | None = None


@dataclass
class CallablePlugin:
    name: str
    module: ModuleType
    func: Callable[..., object] = field(kw_only=True)
    section: str = "scene"
    icon: str = "rocket-launch"
    backend: ModuleType | None = None
    delay: timedelta = field(default_factory=timedelta)


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
    mqtt_user: str = ""
    mqtt_password: str = ""

    # Plugin-consumed secrets; core never reads these. A key with an in-repo
    # consumer belongs on a typed field instead.
    other: dict[str, str] = field(default_factory=dict)

    def __getitem__(self, key: str) -> str:
        try:
            return self.other[key]
        except KeyError:
            raise KeyError(f"secret {key!r} is not set (add it to the secrets provider)") from None


@dataclass
class AppContext:
    """Everything a plugin may touch, so plugins never import orc internals directly.
    The module fields are static; they default to lazy imports because this module
    can't import orc.api at import time (api imports model)."""

    snapshot_manager: SnapshotManager
    scheduler: BaseScheduler
    version_manager: VersionManager
    config: OrcConfig = field(default_factory=lambda: importlib.import_module("orc").config)
    api: ModuleType = field(default_factory=lambda: importlib.import_module("orc.api"))
    orc: ModuleType = field(default_factory=lambda: importlib.import_module("orc"))


class DeviceEnumMeta(EnumType):
    def __sub__(cls, e: set[Any]) -> set[Any]:
        return set(cls) - e


class DeviceEnum(Enum, metaclass=DeviceEnumMeta):
    capabilities: frozenset[Capability]
    room: str | None
    label: str | None

    def __new__(
        cls, value: Any, capabilities: frozenset[Capability] = frozenset(), room: str | None = None, label: str | None = None
    ) -> Self:
        obj = object.__new__(cls)
        if isinstance(value, tuple):
            obj._value_ = value[0]
            obj.capabilities = value[1] if len(value) > 1 else frozenset()
            obj.room = value[2] if len(value) > 2 else None
            obj.label = value[3] if len(value) > 3 else None
        else:
            obj._value_ = value
            obj.capabilities = capabilities
            obj.room = room
            obj.label = label
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
    blueprints: list[tuple[str, str, "Blueprint"]] = field(default_factory=list)


def resolve_time(value: str) -> time | str:
    if value in (SUNRISE, SUNSET):
        return value
    parts = value.split(":")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise ValueError(_ERR_TIME.format(value))
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(_ERR_TIME.format(value))
    return time(hour, minute)


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
