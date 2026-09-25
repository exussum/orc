import importlib
from collections import defaultdict
from collections.abc import Callable, ItemsView, Iterable, ValuesView
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum, EnumType, Flag, StrEnum, auto
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple, Self
from zoneinfo import ZoneInfo

from apscheduler.schedulers.base import BaseScheduler

from orc.kernel import engine

if TYPE_CHECKING:
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import rsa
    from flask import Blueprint

    from orc import Config as OrcConfig
    from orc.view import VersionManager


class Person(NamedTuple):
    host: str
    mac: str


class BleTag(NamedTuple):
    person: str
    secret: str
    pair_date: str


class BleKey(NamedTuple):
    eik: bytes
    anchor: int  # unix seconds of the tag's clock zero (its pair date)


type Listener = Callable[[DeviceState, str, Any, Any], None]
type ButtonListener = Callable[[int, int, str], None]
type DeviceCommand = engine.Command[Any, Devices]
type Commands = tuple[DeviceCommand, ...]


class ThemeOverride(NamedTuple):
    name: str
    start: date
    end: date


class Remote(NamedTuple):
    device: "DeviceEnum"
    button: int
    event: str
    action: str


class Highlight(NamedTuple):
    name: str
    start: time
    stop: time


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
    presence_hours: int = 9
    checkin_hours: int = 1
    sunset_lead_hours: int = 1
    emergency_routine: str | None = None

    @classmethod
    def build(cls, **values: Any) -> Settings:
        if "tz" in values:
            values["tz"] = ZoneInfo(values["tz"])
        return cls(**values)


@dataclass(frozen=True)
class RetryStats:
    id: int
    failed: int
    clean: int
    retried: int


@dataclass(frozen=True)
class DeviceStatus:
    name: str
    details: dict[str, Any]
    label: str | None = None
    action: str | None = None


SUNRISE = "sunrise"
SUNSET = "sunset"

SKIP_REPLAY_TAG = "skip-replay"

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


class Tag(str, Enum):
    SYSTEM = "SYSTEM"
    ANYONE = "ANYONE"


ORC_SYSTEM_SNAPSHOT = "ORC_SYSTEM_SNAPSHOT"


class WeatherCondition(str, Enum):
    SUNNY = "SUNNY"
    CLOUDY = "CLOUDY"


class Alarm(str, Enum):
    WARNING = "WARNING"
    ATTENTION = "ATTENTION"
    EMERGENCY = "EMERGENCY"


class AcState(Flag):
    """An AC's live state: OFF, a powered mode, or bare ON when the mode is unknown.

    ON is the union of the modes, so matching is bitwise containment: ``COOL in ON``
    holds for any powered state, ``ON in COOL`` does not."""

    OFF = auto()
    COOL = auto()
    FAN_ONLY = auto()
    ECON = auto()
    DRY = auto()
    ON = COOL | FAN_ONLY | ECON | DRY


@dataclass(frozen=True)
class CA:
    cert: x509.Certificate
    key: rsa.RSAPrivateKey


@dataclass(frozen=True)
class Certificate:
    cert_pem: bytes
    key_pem: bytes


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


@dataclass(frozen=True)
class Trigger:
    id: str

    def __str__(self) -> str:
        return self.id


@dataclass(frozen=True)
class Broker(Trigger):
    source: str = ""

    def __str__(self) -> str:
        return f"{self.source}:{self.id}"


class Query(Trigger): ...


class Cron(Trigger): ...


class Scheduled(Trigger): ...


class Integration(Trigger): ...


class Manual(Trigger): ...


class System(Trigger): ...


@dataclass
class LogSubEntry:
    timestamp: datetime
    source: LogSourceEnum
    action: str


@dataclass
class LogEntry(LogSubEntry):
    trigger: Trigger | None = None  # what set this off; children inherit it
    children: list[LogSubEntry] = field(default_factory=list)

    def add(self, source: LogSourceEnum, action: str) -> LogSubEntry:
        entry = LogSubEntry(datetime.now(self.timestamp.tzinfo), source, action)
        self.children.append(entry)
        return entry


@dataclass
class IotJob:
    rule: Routine


class Speak(str):
    """A Command value meaning 'speak this text aloud' — distinct from a plain str
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


class AcMode(StrEnum):
    COOL = "cool"
    FAN_ONLY = "fan_only"
    ECON = "econ"
    DRY = "dry"


@dataclass(frozen=True)
class AcCommand:
    mode: AcMode
    fan: str
    temp: int

    def __str__(self) -> str:
        return f"{self.mode}:{self.fan}:{self.temp}"


class Playback(StrEnum):
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"


@dataclass
class SoundState:
    what: DeviceEnum
    content: str | None
    volume: int
    playback: Playback = Playback.STOPPED


@dataclass
class AcStatus:
    what: DeviceEnum
    state: AcState | None
    temperature: int | None = None


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
class AdhocAction:
    commands: Commands
    snapshot: timedelta | None = None
    delay: timedelta = field(default_factory=timedelta)
    section: str | None = None
    reset: bool = True

    def __init__(
        self,
        *commands: DeviceCommand,
        snapshot: timedelta | None = None,
        delay: timedelta = timedelta(),
        section: str | None = None,
        reset: bool = True,
    ) -> None:
        if snapshot and delay:
            raise ValueError("snapshot and delay cannot both be set")
        if snapshot and not reset:
            raise ValueError("snapshot and reset=false cannot both be set")
        self.commands = tuple(commands)
        self.snapshot = snapshot
        self.delay = delay
        self.section = section
        self.reset = reset


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

    # Dynamically named secrets: plugin-consumed keys and per-tag BLE EIKs
    # (named by `tag` config lines). A key with a fixed in-repo consumer
    # belongs on a typed field instead.
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

    scheduler: BaseScheduler
    version_manager: VersionManager
    engine: engine.Runtime = field(default_factory=lambda: engine.Runtime([], bypass=Tag.SYSTEM, override_key=ORC_SYSTEM_SNAPSHOT))
    plugin_state: dict[ModuleType, Any] = field(default_factory=dict)
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
        obj._value_ = value
        obj.capabilities = capabilities
        obj.room = room
        obj.label = label
        return obj

    @property
    def kind(self) -> str:
        return type(self).__name__


@dataclass(frozen=True)
class Devices(engine.Channel):
    members: tuple[DeviceEnum, ...]

    def __init__(self, what: "DeviceEnum | type[DeviceEnum] | Iterable[DeviceEnum] | Devices") -> None:
        if isinstance(what, Devices):
            members = what.members
        elif isinstance(what, Enum):
            members = (what,)
        else:
            members = tuple(what)
        object.__setattr__(self, "members", members)

    def all(self) -> tuple[DeviceEnum, ...]:
        return self.members

    def one(self) -> DeviceEnum:
        if len(self.members) != 1:
            raise ValueError(f"expected exactly one device, got {len(self.members)}: {self.members}")
        return self.members[0]


SnapShot = engine.SnapShot[Devices]
Routine = engine.Rule[Devices]


@dataclass(frozen=True)
class PersonChannel(engine.Channel):
    name: str


@dataclass(frozen=True)
class AnyoneChannel(engine.Channel):
    pass


@dataclass(frozen=True)
class WeatherChannel(engine.Channel):
    pass


PresenceChannel = PersonChannel | AnyoneChannel


@dataclass(frozen=True)
class MqttDeviceChannel(engine.Channel):
    device: DeviceEnum
    attribute: str


@dataclass(frozen=True)
class AcChannel(engine.Channel):
    device: DeviceEnum


@dataclass(frozen=True)
class CastChannel(engine.Channel):
    device: DeviceEnum


class DeviceNamespace(SimpleNamespace):
    """Device type name -> enum class, built fresh per config load, reached as
    ``registry.devices.Light`` (dot access, no per-device wrapper). A name in
    _KNOWN_DEVICE_TYPES that this particular config never declared resolves to an
    empty enum instead of raising, so core code can read e.g. ``registry.devices.Sensor``
    unconditionally regardless of whether a given config declares that type."""

    _KNOWN_DEVICE_TYPES: ClassVar[frozenset[str]] = frozenset({"Light", "Chromecast", "BroadLink", "AC", "USB", "Sensor"})

    def __getattr__(self, name: str) -> type[DeviceEnum]:
        if name not in self._KNOWN_DEVICE_TYPES:
            raise AttributeError(name)
        return DeviceEnum(name, {}, module="orc")  # type: ignore[call-arg,arg-type,return-value]

    def __contains__(self, name: str) -> bool:
        return name in vars(self)

    def items(self) -> ItemsView[str, type[DeviceEnum]]:
        return vars(self).items()

    def values(self) -> ValuesView[type[DeviceEnum]]:
        return vars(self).values()


@dataclass
class Registry:
    """What plugins registered, built per config load and exposed as
    ``orc.config.registry``.

    ``scripts`` maps served filename to the plugin's JS file on disk; all enabled
    plugins' files are served in the ``/hooks.js`` bundle and register themselves
    with the browser hooks. ``button_labels`` are keyed by button/action id, not device
    type, so they sit alongside ``devices``. ``device_icons``/``controllable_devices``/
    ``dispatch_handlers`` are likewise keyed by device type name, alongside ``devices``
    rather than folding into a per-device wrapper record.
    ``state_providers`` are registered by setup hooks (``api.add_state_provider``)
    and called fresh by consumers on each request, so the returned rows reflect live
    device state."""

    devices: DeviceNamespace
    device_icons: dict[str, str]
    controllable_devices: frozenset[str]
    dispatch_handlers: dict[str, Callable[..., None]]
    scripts: dict[str, Path]
    button_labels: dict[str, str]
    state_providers: dict[str, Callable[[], Any]]
    setup_hooks: list[Callable[[AppContext], None]]
    blueprints: list[tuple[str, str, "Blueprint"]] = field(default_factory=list)
    # Set by a setup hook (``api.set_ac_handler``); ``api.ac_command`` calls it with the
    # target AC device, so one backend routes every AC member by device.
    ac_handler: Callable[["DeviceEnum", str | None, str | None, str | None, int | None], None] | None = None
    # Set by a setup hook (``api.set_ac_state_handler``); ``api.ac_state`` reads a
    # device's live ``AcState`` (None if unknown) through it.
    ac_state_handler: Callable[["DeviceEnum"], AcState | None] | None = None
    # Set by a setup hook (``api.set_ac_temperature_handler``); ``api.ac_temperature``
    # reads a device's setpoint in °F (None if unknown or the unit is off) through it.
    ac_temperature_handler: Callable[["DeviceEnum"], int | None] | None = None


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


def squish(
    commands: Iterable[DeviceCommand],
    *,
    state_override: Any = None,
    on_conflict: Callable[[Any, list[Any]], object] = lambda what, states: None,
) -> Commands:
    """Merge commands as if run sequentially — dedupe per device, handle brightness/stop changes."""
    grouped: defaultdict[Any, list[DeviceCommand]] = defaultdict(list)
    for command in commands:
        for e in command.channel.all():
            value = command.value if state_override is None else state_override
            grouped[e].append(engine.Command(Devices(e), value, command.tag))

    flattened: list[DeviceCommand] = []
    for what, items in grouped.items():
        squished = _squish(items)
        if {c.value for c in items} - {c.value for c in squished}:
            on_conflict(what, [c.value for c in items])
        flattened.extend(squished)

    flattened.sort(key=_op_cmp)
    return tuple(flattened)


def _op_cmp(k: DeviceCommand) -> tuple[int, int]:
    # types never declared controllable tie past everything registered
    class_sort = _CLASS_SORT.get(type(k.channel.one()).__name__, len(_CLASS_SORT))

    if k.value == STOP:
        sub_sort = _STATE_SORT_STOP
    elif isinstance(k.value, int):
        sub_sort = _STATE_SORT_INT
    elif k.value == ON:
        sub_sort = _STATE_SORT_ON
    else:
        sub_sort = _STATE_SORT_OTHER
    return (class_sort, sub_sort)


def _squish(items: list[DeviceCommand]) -> tuple[DeviceCommand, ...]:
    if not items:
        return ()

    last = items[-1]
    # a level (int) is preceded by the last STOP; a non-level is preceded by the last level
    preceding = (lambda c: c.value == STOP) if isinstance(last.value, int) else (lambda c: isinstance(c.value, int))
    match = next((item for item in reversed(items[:-1]) if preceding(item)), None)
    return (match, last) if match else (last,)
