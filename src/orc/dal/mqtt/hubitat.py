"""Hubitat MQTT Export codec, and for now the broker connection it rides.

``Hubitat`` turns the hub's retained per-device JSON documents on
``hubitat/<hub-uuid>/devices/<id>`` into device statuses and light commands into
publishes; the hub uuid is captured from the first message rather than
configured. State, events, commands and discovery all flow through here; only
hub reboot stays on Maker API.
"""

import functools
import json
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar

import paho.mqtt.client as mqtt

import orc
from orc import model as m
from orc.collections import LockedDict
from orc.dal import sqlite
from orc.dal.mqtt import switch_on

REQUIRED_SECRETS: dict[str, Callable[[str], Any]] = {}

_log = logging.getLogger(__name__)

_MQTT_PORT = 1883
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


class Hubitat:
    namespaces: tuple[str, ...] = ("hubitat",)
    device_types: tuple[str, ...] = ("Light", "Button", "Sensor")

    def __init__(self) -> None:
        self.hub_id: str | None = None  # written only by the mqtt thread
        self._devices: LockedDict[str, m.DeviceState] = LockedDict()

        # Command round-trip measurement: encode stamps the command topic at send, and
        # the broker echoing our own publish back on the hubitat/# subscription pops it.
        # Keyed by topic, so the size is bounded by distinct (device, command) pairs; an
        # entry whose echo never arrives (broker down at publish) is overwritten by the
        # topic's next send. Round trips fold into orc_durations, keyed by command topic.
        self._command_sent: LockedDict[str, float] = LockedDict()

        # External-control detection: a switch/level change no pending _Command explains
        # is reported as external (Google Home, a physical switch).
        self._commanded: LockedDict[str, _Command] = LockedDict()

    def attach(self, publish: Callable[[m.Message], None]) -> None:
        pass

    def decode(self, topic: str, doc: dict[str, Any]) -> tuple[m.Status, ...]:
        parts = topic.split("/")
        if len(parts) < 4 or parts[2] != "devices":
            return ()  # location/variables topics
        self.hub_id = parts[1]
        kind = parts[4] if len(parts) > 4 else None
        if kind is None:
            return self._document(topic, doc)
        elif kind == "button" and doc:  # the empty clearing publish follows each event
            return self._button(topic, doc)
        elif kind == "commands":
            self._echo(topic)
        return ()

    def encode(self, device: m.DeviceEnum, command: Any) -> tuple[m.Message, ...]:
        asked: _Command
        if isinstance(command, int) and m.Capability.change_level in device.capabilities:
            asked = _Level(level=command)
        else:
            asked = _Switch(on=switch_on(device, command))
        if self.hub_id is None:
            raise RuntimeError(f"hub not yet seen; cannot command {device.name}")
        message = asked.message(self.hub_id, device.value)
        self._command_sent[message.topic] = asked.sent
        self._commanded[device.value] = asked
        return (message,)

    def snapshot(self) -> tuple[m.DeviceState, ...]:
        return tuple(sorted(self._devices.values(), key=lambda state: state.device.id))

    def start(self) -> None:
        pass

    # State is never an event: first sightings (the retained flood at boot) and replays
    # (a document identical to the cached one — reconnect floods, hub republish after
    # reboot) update the cache but yield nothing. No other dedup: the hub regenerates
    # the document only when something happens, so a changed document yields every
    # attribute, unchanged ones included (old == new); consumers filter for what they
    # care about. A commandable attribute that moved has source EXTERNAL when no pending
    # command explains it and ORC when one does; everything else the device itself reports.
    def _document(self, topic: str, doc: dict[str, Any]) -> tuple[m.Status, ...]:
        state = parse_document(doc)
        if state is None:
            _log.error("mqtt: bad device document on %s: %r", topic, doc)
            return ()
        id = state.device.id
        old, self._devices[id] = self._devices.get(id), state
        if old is None or old == state:
            return ()
        now, expected = time.monotonic(), self._commanded.get(id)
        sources: dict[str, m.SourceEnum] = {}
        for kind in _COMMANDS:
            before, after = old.attributes.get(kind.attribute), state.attributes.get(kind.attribute)
            if expected is not None and expected.explains(kind.attribute, after, now):
                sources[kind.attribute] = m.Source.ORC
            elif before is not None and after is not None and not kind.same(before, after):
                sources[kind.attribute] = m.Source.EXTERNAL
        if expected is not None and expected.satisfied_by(state.attributes, now):
            self._commanded.pop(id)
        return tuple(
            m.Status(state.device, a, old.attributes.get(a), v, sources.get(a, HubitatSource.HUBITAT)) for a, v in state.attributes.items()
        )

    # A press is the one status with nothing before it: (device, event type, None,
    # button number), from a dedicated event message that is never retained or replayed.
    def _button(self, topic: str, doc: dict[str, Any]) -> tuple[m.Status, ...]:
        try:
            device_id, button, event_type = topic.split("/")[3], int(doc["button"]), doc["event_type"]
        except ValueError, KeyError, TypeError:
            _log.exception("mqtt: bad button event on %s", topic)
            return ()
        known = self._devices.get(device_id)
        if known is None:
            return ()
        return (m.Status(known.device, event_type, None, button, HubitatSource.HUBITAT),)

    def _echo(self, topic: str) -> None:
        sent = self._command_sent.pop(topic)
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


_client: mqtt.Client | None = None  # the standing client, retained for publishing commands
_listeners: list[m.Listener] = []  # run on the mqtt thread; keep them fast and don't block
_external_listeners: list[m.Listener] = []


def add_listener(fn: m.Listener) -> None:
    _listeners.append(fn)


def add_external_listener(fn: m.Listener) -> None:
    _external_listeners.append(fn)


def start() -> None:
    global _client
    _client = _new_client(orc.config.secrets, _on_connect, _on_message, 3.0)
    _codec.start()


def snapshot() -> list[m.DeviceState]:
    return list(_codec.snapshot())


def fetch_hubitat_config(secrets: m.Secrets, timeout: float = 3.0) -> dict[str, tuple[str, frozenset[m.Capability]]]:
    found: dict[str, tuple[str, frozenset[m.Capability]]] = {}
    retained: set[str] = set()

    def on_message(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        parts = msg.topic.split("/")
        state = parse_document(_parse(msg.payload)) if len(parts) == 4 and parts[2] == "devices" else None
        if state is not None:
            dimmable = "level" in state.attributes
            found[state.device.name] = (state.device.id, frozenset([m.Capability.change_level]) if dimmable else frozenset())
            if msg.retain:
                retained.add(state.device.name)

    client = _new_client(secrets, _on_connect, on_message, timeout)
    client.loop_stop()
    client.disconnect()
    if not found:
        raise RuntimeError("mqtt: device discovery found no device documents; broker unreachable or MQTT credentials missing")

    # the cache, boot discovery and every reconnect lean on the hub publishing retained documents
    if unretained := sorted(found.keys() - retained):
        raise RuntimeError(f"mqtt: device documents are not retained for {', '.join(unretained)}; turn retain on in the MQTT Export app")
    return found


def _connected[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        if _client is None:
            raise RuntimeError(f"mqtt client not started; cannot {fn.__name__}")
        return fn(*args, **kwargs)

    return wrapper


@_connected
def publish_light(light: m.DeviceEnum, on: bool | None = None, brightness: int | None = None) -> None:
    for message in _codec.encode(light, brightness if brightness is not None else (m.ON if on else m.OFF)):
        _publish(message)


def _publish(message: m.Message) -> None:
    assert _client is not None
    payload = json.dumps(message.payload) if isinstance(message.payload, dict) else message.payload
    _client.publish(message.topic, payload, retain=message.retain)


_codec = Hubitat()
_codec.attach(_publish)


def _new_client(secrets: m.Secrets, on_connect: Callable[..., None], on_message: Callable[..., None], timeout: float) -> mqtt.Client:
    received = 0

    def counting(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        nonlocal received
        received += msg.retain
        on_message(client, userdata, msg)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(secrets.mqtt_user, secrets.mqtt_password)
    client.on_connect = on_connect
    client.on_message = counting
    client.reconnect_delay_set(min_delay=1, max_delay=60)
    client.connect_async(orc.config.settings.mqtt_host, _MQTT_PORT, keepalive=30)
    client.loop_start()
    if not _wait_settled(lambda: received, timeout):
        _log.warning("mqtt: retained documents still arriving after %.0fs (%d so far)", timeout, received)
    client.on_message = on_message
    return client


def _on_connect(client: mqtt.Client, userdata: Any, flags: Any, rc: Any, *args: Any) -> None:
    if rc != 0:
        # Bad credentials land here and paho retries quietly forever; make it loud.
        _log.warning("mqtt: connect refused: %s", rc)
        return
    # Subscribe after CONNACK: paho drops (does not queue) subscriptions made earlier.
    client.subscribe("hubitat/#", qos=0)


def _wait_settled(count: Callable[[], int], timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    previous = -1
    while time.monotonic() < deadline:
        time.sleep(0.25)
        current = count()
        if current and current == previous:
            return True
        previous = current
    return False


def _on_message(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
    try:
        statuses = _codec.decode(msg.topic, _parse(msg.payload))
    except Exception:
        _log.exception("mqtt: message handling failed for %s", msg.topic)
        return
    for status in statuses:
        if status.source == m.Source.EXTERNAL:
            _fire(_external_listeners, msg.topic, status.device, status.attribute, status.old, status.new)
    for status in statuses:
        _fire(_listeners, msg.topic, status.device, status.attribute, status.old, status.new)


# The codec always sees a dict: the parsed object, or {} for an empty payload (the
# hub's command echoes and button-clearing publishes), a bare value (a setLevel
# echo), or anything that isn't JSON. The AC appends a null terminator to its JSON.
def _parse(payload: bytes) -> dict[str, Any]:
    try:
        doc = json.loads(payload.rstrip(b"\x00")) if payload else {}
    except ValueError:
        return {}
    return doc if isinstance(doc, dict) else {}


def _fire(listeners: Sequence[Callable[..., None]], topic: str, *args: Any) -> None:
    for listener in list(listeners):
        try:
            listener(*args)
        except Exception:
            _log.exception("mqtt: listener failed for %s", topic)
