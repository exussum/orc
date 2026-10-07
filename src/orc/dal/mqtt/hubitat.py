"""Hubitat MQTT Export adapter.

Turns the hub's retained per-device JSON documents on
``hubitat/<hub-uuid>/devices/<id>`` into device statuses and light commands into
publishes; the hub uuid is captured from the first message rather than
configured. Everything but hub reboot (Maker API) flows through here.
"""

import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar

from orc import model as m
from orc.collections import LockedDict
from orc.dal import sqlite
from orc.dal.mqtt import switch_on

REQUIRED_SECRETS: dict[str, Callable[[str], Any]] = {}

_log = logging.getLogger(__name__)

_COMMAND_TTL_SEC = 120.0
_SOURCE = "hubitat"


@dataclass(frozen=True)
class _Command(ABC):
    attribute: ClassVar[str]
    sent: float = field(default_factory=time.monotonic, kw_only=True)

    @abstractmethod
    def message(self, hub_id: str, device_id: str) -> m.Message: ...

    @abstractmethod
    def expects(self) -> dict[str, Any]: ...

    @staticmethod
    def same(a: Any, b: Any) -> bool:
        return a == b

    def explains(self, attribute: str, reported: Any, now: float) -> bool:
        asked = self.expects().get(attribute)
        return asked is not None and reported is not None and now - self.sent <= _COMMAND_TTL_SEC and _kind(attribute).same(asked, reported)

    # an attribute the report leaves out is not held against the command
    def satisfied_by(self, attributes: dict[str, Any], now: float) -> bool:
        return all(attributes.get(a) is None or self.explains(a, attributes.get(a), now) for a in self.expects())


@dataclass(frozen=True)
class _Switch(_Command):
    attribute = "switch"
    on: bool

    def message(self, hub_id: str, device_id: str) -> m.Message:
        return m.Message(f"hubitat/{hub_id}/devices/{device_id}/commands/{m.ON if self.on else m.OFF}", None)

    def expects(self) -> dict[str, Any]:
        return {"switch": m.ON if self.on else m.OFF}


@dataclass(frozen=True)
class _Level(_Command):
    attribute = "level"
    level: int

    def message(self, hub_id: str, device_id: str) -> m.Message:
        return m.Message(f"hubitat/{hub_id}/devices/{device_id}/commands/setLevel", str(self.level))

    def expects(self) -> dict[str, Any]:
        return {"switch": m.ON, "level": self.level} if self.level else {"switch": m.OFF}

    @staticmethod
    def same(a: Any, b: Any) -> bool:
        return abs(int(a) - int(b)) <= 1  # drivers round through the 0-254 scale


_COMMANDS = (_Switch, _Level)


def _kind(attribute: str) -> type[_Command]:
    return next((kind for kind in _COMMANDS if kind.attribute == attribute), _Command)


class HubitatSource(m.SourceEnum):
    HUBITAT = "hubitat"


namespaces: tuple[str, ...] = ("hubitat",)
device_types: tuple[str, ...] = ("Light", "Button", "Sensor")

hub_id: str | None = None  # written only by the mqtt thread
_devices: LockedDict[str, m.DeviceState] = LockedDict()

# Command round-trip measurement: encode stamps the command topic at send, and
# the broker echoing our own publish back on the hubitat/# subscription pops it.
# Keyed by topic, so the size is bounded by distinct (device, command) pairs; an
# entry whose echo never arrives (broker down at publish) is overwritten by the
# topic's next send. Round trips fold into orc_durations, keyed by command topic.
_command_sent: LockedDict[str, float] = LockedDict()

# External-control detection: a switch/level change no pending _Command explains
# is reported as external (Google Home, a physical switch).
_commanded: LockedDict[str, _Command] = LockedDict()


def attach(publish: Callable[[m.Message], None]) -> None:
    pass


def decode(topic: str, doc: dict[str, Any]) -> tuple[m.Status, ...]:
    global hub_id
    parts = topic.split("/")
    if len(parts) < 4 or parts[2] != "devices":
        return ()  # location/variables topics
    hub_id = parts[1]
    kind = parts[4] if len(parts) > 4 else None
    if kind is None:
        return _document(topic, doc)
    elif kind == "button" and doc:  # the empty clearing publish follows each event
        return _button(topic, doc)
    elif kind == "commands":
        _echo(topic)
    return ()


def encode(device: m.DeviceEnum, command: Any) -> tuple[m.Message, ...]:
    asked: _Command
    if isinstance(command, int) and m.Capability.change_level in device.capabilities:
        asked = _Level(level=command)
    else:
        asked = _Switch(on=switch_on(device, command))
    if hub_id is None:
        raise RuntimeError(f"hub not yet seen; cannot command {device.name}")
    message = asked.message(hub_id, device.value)
    _command_sent[message.topic] = asked.sent
    _commanded[device.value] = asked
    return (message,)


def snapshot() -> tuple[m.DeviceState, ...]:
    return tuple(sorted(_devices.values(), key=lambda state: state.device.id))


def start() -> None:
    pass


def discover(messages: Sequence[m.Message]) -> dict[str, tuple[str, frozenset[m.Capability]]]:
    found: dict[str, tuple[str, frozenset[m.Capability]]] = {}
    retained: set[str] = set()
    for message in messages:
        parts = message.topic.split("/")
        state = parse_document(message.payload) if len(parts) == 4 and parts[2] == "devices" and isinstance(message.payload, dict) else None
        if state is not None:
            dimmable = "level" in state.attributes
            found[state.device.name] = (state.device.id, frozenset([m.Capability.change_level]) if dimmable else frozenset())
            if message.retain:
                retained.add(state.device.name)
    if not found:
        raise RuntimeError("mqtt: device discovery found no device documents; broker unreachable or MQTT credentials missing")

    # the cache, boot discovery and every reconnect lean on the hub publishing retained documents
    if unretained := sorted(found.keys() - retained):
        raise RuntimeError(f"mqtt: device documents are not retained for {', '.join(unretained)}; turn retain on in the MQTT Export app")
    return found


# State is never an event: first sightings (the retained flood at boot) and replays
# (a document identical to the cached one — reconnect floods, hub republish after
# reboot) update the cache but yield nothing. No other dedup: the hub regenerates
# the document only when something happens, so a changed document yields every
# attribute, unchanged ones included (old == new); consumers filter for what they
# care about. A commandable attribute that moved has source EXTERNAL when no pending
# command explains it and ORC when one does; everything else the device itself reports.
def _document(topic: str, doc: dict[str, Any]) -> tuple[m.Status, ...]:
    state = parse_document(doc)
    if state is None:
        _log.error("mqtt: bad device document on %s: %r", topic, doc)
        return ()
    id = state.device.id
    old, _devices[id] = _devices.get(id), state
    if old is None or old == state:
        return ()
    now, expected = time.monotonic(), _commanded.get(id)
    sources: dict[str, m.SourceEnum] = {}
    for kind in _COMMANDS:
        before, after = old.attributes.get(kind.attribute), state.attributes.get(kind.attribute)
        if expected is not None and expected.explains(kind.attribute, after, now):
            sources[kind.attribute] = m.Source.ORC
        elif before is not None and after is not None and not kind.same(before, after):
            sources[kind.attribute] = m.Source.EXTERNAL
    if expected is not None and expected.satisfied_by(state.attributes, now):
        _commanded.pop(id)
    return tuple(
        m.Status(state.device, a, old.attributes.get(a), v, sources.get(a, HubitatSource.HUBITAT)) for a, v in state.attributes.items()
    )


# A press is the one status with nothing before it: (device, event type, None,
# button number), from a dedicated event message that is never retained or replayed.
def _button(topic: str, doc: dict[str, Any]) -> tuple[m.Status, ...]:
    try:
        device_id, button, event_type = topic.split("/")[3], int(doc["button"]), doc["event_type"]
    except ValueError, KeyError, TypeError:
        _log.exception("mqtt: bad button event on %s", topic)
        return ()
    known = _devices.get(device_id)
    if known is None:
        return ()
    return (m.Status(known.device, event_type, None, button, HubitatSource.HUBITAT),)


def _echo(topic: str) -> None:
    sent = _command_sent.pop(topic)
    if sent is not None:
        sqlite.update_avg(topic, time.monotonic() - sent)


def parse_document(doc: dict[str, Any]) -> m.DeviceState | None:
    try:
        return m.DeviceState(
            m.Device(str(doc["id"]), doc["name"], _SOURCE),
            attributes={a["name"]: a["value"] for a in doc["attributes"]},
            last_activity=doc.get("lastActivity"),
        )
    except ValueError, KeyError, TypeError:
        return None
