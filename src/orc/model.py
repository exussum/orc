import importlib
import math
from collections import defaultdict
from collections.abc import Callable, ItemsView, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum, EnumType, StrEnum, auto
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple, Protocol, Self
from zoneinfo import ZoneInfo

from apscheduler.job import Job
from orc_engine import cast, engine
from orc_engine import model as em

if TYPE_CHECKING:
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import rsa
    from flask import Blueprint

    from orc import Config as OrcConfig


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


class TagClock(NamedTuple):
    offset: int  # tag clock minus the pair-date estimate, in seconds
    heard: int
    eid: bytes | None = None


type Listener = Callable[[Device, str, Any, Any], None]
type DeviceCommand = em.Command[Any, Devices]
type Commands = tuple[DeviceCommand, ...]


class ThemeOverride(NamedTuple):
    name: str
    start: date
    end: date


class PushSubscription(NamedTuple):
    endpoint: str
    public_key: str
    auth_secret: str


class Notification(NamedTuple):
    tag: tuple[str, ...]
    url: str = ""


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
UNASSIGNED_ROOM = "Unassigned"


_STATE_SORT_STOP = -2
_STATE_SORT_INT = -1
_STATE_SORT_ON = 0
_STATE_SORT_OTHER = 1


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


@dataclass(frozen=True)
class CA:
    cert: x509.Certificate
    key: rsa.RSAPrivateKey


@dataclass(frozen=True)
class Certificate:
    cert_pem: bytes
    key_pem: bytes


@dataclass(frozen=True)
class Device:
    id: str
    name: str
    source: str


@dataclass(frozen=True)
class DeviceState:
    device: Device
    attributes: dict[str, Any]
    last_activity: str | None


class SourceEnum(StrEnum): ...


class Source(SourceEnum):
    ORC = "orc"
    EXTERNAL = "external"


@dataclass(frozen=True)
class Status:
    device: Device
    attribute: str
    old: Any
    new: Any
    source: SourceEnum


@dataclass(frozen=True)
class Message:
    topic: str
    payload: dict[str, Any] | str | None
    retain: bool = False


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
    value: Any = None

    def __str__(self) -> str:
        return f"{self.source}:{self.id}"


class Query(Trigger): ...


class Cron(Trigger): ...


class Scheduled(Trigger): ...


class Integration(Trigger): ...


class Manual(Trigger): ...


class Button(Trigger): ...


@dataclass(frozen=True)
class Request(Trigger):
    command: Any

    def answered_by(self, response: Broker) -> bool:
        if self.id != response.id:
            return False
        return self.command == response.value


class System(Trigger): ...


@dataclass
class LogSubEntry:
    timestamp: datetime
    source: LogSourceEnum
    action: str
    notified: bool = field(default=False, kw_only=True)


@dataclass
class LogEntry(LogSubEntry):
    trigger: Trigger
    children: list[LogSubEntry] = field(default_factory=list)
    requests: tuple[Request, ...] = ()

    def add(self, source: LogSourceEnum, action: str, *, notified: bool = False) -> LogSubEntry:
        entry = LogSubEntry(datetime.now(self.timestamp.tzinfo), source, action, notified=notified)
        self.children.append(entry)
        return entry

    def answer(self, response: Trigger) -> bool:
        if not isinstance(response, Broker) or response.value is None:
            return False
        pending = tuple(r for r in self.requests if not r.answered_by(response))
        answered = pending != self.requests
        self.requests = pending
        return answered


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
class AcState:
    """What an AC is doing, or what a command asks of it: a field left None is unsaid."""

    power: str | None = None
    mode: str | None = None
    fan_mode: str | None = None
    current_temperature: float | None = field(default=None, compare=False, kw_only=True)  # the room's; moves on its own
    temperature: float | None = None

    def __str__(self) -> str:
        if self.power == OFF:
            return OFF
        elif self.mode and self.fan_mode and self.temperature is not None:
            return f"{self.mode}:{self.fan_mode}:{round(self.temperature)}"
        return self.power or "?"


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


class ThemeEntry(NamedTuple):
    when: time | str
    routine: Routine


@dataclass
class Theme:
    name: str
    entries: tuple[ThemeEntry, ...] = ()


@dataclass
class Secrets:
    hubitat_access_token: str = ""
    market_holidays_url: str = ""
    mqtt_user: str = ""
    mqtt_password: str = ""
    vapid_private_key: str = ""

    # Dynamically named secrets: plugin-consumed keys and per-tag BLE EIKs
    # (named by `tag` config lines). A key with a fixed in-repo consumer
    # belongs on a typed field instead.
    other: dict[str, str] = field(default_factory=dict)


class Scheduler(Protocol):
    def start(self, ctx: "AppContext") -> None: ...
    def resume(self) -> None: ...
    def once(self, func: Any, when: datetime, *args: Any, id: str | None = None, name: str | None = None, persist: bool = False) -> Job: ...
    def now(self, func: Any, *args: Any, name: str | None = None, skip_if_late: bool = False) -> Job: ...
    def cron(self, func: Any, crontab: str, *args: Any, id: str, name: str) -> Job: ...
    def cancel(self, id: str) -> bool: ...
    def set_paused(self, id: str, paused: bool) -> bool: ...
    def matching(self, type: type) -> list[Job]: ...
    def stores(self) -> list[tuple[str, list[Job]]]: ...
    def on_rebuild(self, callback: Callable[[], None]) -> None: ...
    def rebuild(self) -> None: ...
    def delete_stale(self) -> None: ...


@dataclass
class AppContext:
    """Everything a plugin may touch, so plugins never import orc internals directly.
    The module fields are static; they default to lazy imports because this module
    can't import orc.api at import time (api imports model)."""

    scheduler: Scheduler
    engine: engine.Runtime
    plugin_state: dict[ModuleType, Any] = field(default_factory=dict)
    config: OrcConfig = field(default_factory=lambda: importlib.import_module("orc").config)
    api: ModuleType = field(default_factory=lambda: importlib.import_module("orc.api"))
    orc: ModuleType = field(default_factory=lambda: importlib.import_module("orc"))


class DeviceEnumMeta(EnumType):
    _sort: float = math.inf

    def __sub__(cls, e: set[Any]) -> set[Any]:
        return set(cls) - e


class DeviceEnum(Enum, metaclass=DeviceEnumMeta):
    capabilities: frozenset[Capability]
    room: str
    label: str | None

    def __new__(
        cls, value: Any, capabilities: frozenset[Capability] = frozenset(), room: str | None = None, label: str | None = None
    ) -> Self:
        obj = object.__new__(cls)
        obj._value_ = value
        obj.capabilities = capabilities
        obj.room = room or UNASSIGNED_ROOM
        obj.label = label
        return obj

    @property
    def kind(self) -> str:
        return type(self).__name__


@dataclass(frozen=True)
class Devices:
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


SnapShot = em.SnapShot[Devices]
Routine = em.Rule[Devices]


@dataclass(frozen=True)
class AdhocAction(em.Action[Devices]):
    delay: timedelta = timedelta()
    snapshot: timedelta | None = None
    section: str | None = None
    reset: bool = True

    def __post_init__(self) -> None:
        if self.snapshot and self.delay:
            raise ValueError("snapshot and delay cannot both be set")
        if self.snapshot and not self.reset:
            raise ValueError("snapshot and reset=false cannot both be set")


@dataclass(frozen=True)
class PersonSubject(em.Subject):
    name: str


@dataclass(frozen=True)
class AnyoneSubject(em.Subject):
    pass


@dataclass(frozen=True)
class WeatherSubject(em.Subject):
    pass


@dataclass(frozen=True)
class OutsideTemperatureSubject(em.Subject):
    pass


@dataclass(frozen=True)
class Present(em.Condition):
    name: str

    def holds(self, world: em.World) -> bool:
        return world.read(PersonSubject(self.name)) is True


@dataclass(frozen=True)
class Anyone(em.Condition):
    def holds(self, world: em.World) -> bool:
        return world.read(AnyoneSubject()) is True


@dataclass(frozen=True)
class Weather(em.Condition):
    condition: WeatherCondition

    def holds(self, world: em.World) -> bool:
        return self.condition in cast.instance(world.read(WeatherSubject()), frozenset)


class DeviceNamespace:
    """Device type name -> enum class, built fresh per config load, reached as
    ``registry.devices.Light`` (dot access, no per-device wrapper). A name in
    _KNOWN_DEVICE_TYPES that this particular config never declared resolves to an
    empty enum instead of raising, so core code can read e.g. ``registry.devices.Sensor``
    unconditionally regardless of whether a given config declares that type."""

    _KNOWN_DEVICE_TYPES: ClassVar[frozenset[str]] = frozenset({"Light", "Chromecast", "BroadLink", "AC", "USB", "Sensor"})

    def __init__(self, **enums: type[DeviceEnum]) -> None:
        vars(self).update(enums)

    def __getattr__(self, name: str) -> type[DeviceEnum]:
        if name not in self._KNOWN_DEVICE_TYPES:
            raise AttributeError(name)
        return DeviceEnum(name, {}, module="orc")  # type: ignore[call-arg,arg-type,return-value]

    def __contains__(self, name: str) -> bool:
        return name in vars(self)

    def items(self) -> ItemsView[str, type[DeviceEnum]]:
        return vars(self).items()


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
    secrets: dict[str, Callable[[str], Any]] = field(default_factory=dict)


def squish(
    commands: Iterable[DeviceCommand],
    *,
    state_override: Any = None,
    on_conflict: Callable[[Any, list[Any]], object] = lambda what, states: None,
) -> Commands:
    """Merge commands as if run sequentially — dedupe per device, handle brightness/stop changes."""
    grouped: defaultdict[Any, list[DeviceCommand]] = defaultdict(list)
    for command in commands:
        for e in command.subject.all():
            value = command.value if state_override is None else state_override
            grouped[e].append(em.Command(Devices(e), value, command.tag))

    flattened: list[DeviceCommand] = []
    for what, items in grouped.items():
        squished = _squish(items)
        if {c.value for c in items} - {c.value for c in squished}:
            on_conflict(what, [c.value for c in items])
        flattened.extend(squished)

    flattened.sort(key=_op_cmp)
    return tuple(flattened)


def _op_cmp(k: DeviceCommand) -> tuple[float, int]:
    class_sort = type(k.subject.one())._sort
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
