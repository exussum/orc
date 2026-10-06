"""The broker connection: one paho client shared by every codec.

Inbound messages are routed by their first topic segment to the codec that
declared that namespace; the statuses it decodes fan out to the listeners, the
external ones to the external listeners as well. Commands route by device type to
the codec's ``encode``, and a codec answers its own protocol through the publisher
attached at registration. The Hubitat codec is built in; plugins register theirs
during setup.
"""

import functools
import json
import logging
import time
from collections.abc import Callable, Sequence
from functools import partial
from typing import TYPE_CHECKING, Any

import paho.mqtt.client as mqtt

import orc
from orc import model as m
from orc.dal.mqtt import hubitat

if TYPE_CHECKING:
    from orc.dal.interfaces import Codec

REQUIRED_SECRETS: dict[str, Callable[[str], Any]] = {}

_log = logging.getLogger(__name__)

_MQTT_PORT = 1883

_hubitat = hubitat.Hubitat()
_codecs: list["Codec"] = [_hubitat]
_client: mqtt.Client | None = None  # the standing client, retained for publishing commands
_listeners: list[m.Listener] = []  # run on the mqtt thread; keep them fast and don't block
_external_listeners: list[m.Listener] = []


def register(codec: "Codec") -> None:
    for other in _codecs:
        if taken := set(other.namespaces) & set(codec.namespaces) or set(other.device_types) & set(codec.device_types):
            raise ValueError(f"mqtt: {', '.join(sorted(taken))} already handled by {type(other).__name__}")
    _codecs.append(codec)
    codec.attach(partial(_publish, codec))


def add_listener(fn: m.Listener) -> None:
    _listeners.append(fn)


def add_external_listener(fn: m.Listener) -> None:
    _external_listeners.append(fn)


def start() -> None:
    global _client
    _client = _new_client(orc.config.secrets, _on_connect, _on_message, 3.0)
    for codec in _codecs:
        codec.start()


def snapshot() -> list[m.DeviceState]:
    return [state for codec in _codecs for state in codec.snapshot()]


def _connected[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        if _client is None:
            raise RuntimeError(f"mqtt client not started; cannot {fn.__name__}")
        return fn(*args, **kwargs)

    return wrapper


@_connected
def command(device: m.DeviceEnum, value: Any) -> None:
    codec = next((c for c in _codecs if device.kind in c.device_types), None)
    if codec is None:
        raise LookupError(f"mqtt: no codec speaks for `{device.kind}`")
    for message in codec.encode(device, value):
        _publish(codec, message)


def fetch_hubitat_config(secrets: m.Secrets, timeout: float = 3.0) -> dict[str, tuple[str, frozenset[m.Capability]]]:
    found: dict[str, tuple[str, frozenset[m.Capability]]] = {}
    retained: set[str] = set()

    def on_message(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        parts = msg.topic.split("/")
        state = hubitat.parse_document(_parse(msg.payload)) if len(parts) == 4 and parts[:3:2] == ["hubitat", "devices"] else None
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


def _publish(codec: "Codec", message: m.Message) -> None:
    assert _client is not None
    if message.topic.split("/", 1)[0] not in codec.namespaces:
        raise ValueError(f"mqtt: {type(codec).__name__} may not publish on {message.topic}")
    payload = json.dumps(message.payload) if isinstance(message.payload, dict) else message.payload
    _client.publish(message.topic, payload, retain=message.retain)


_hubitat.attach(partial(_publish, _hubitat))


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
    for codec in _codecs:
        for namespace in codec.namespaces:
            client.subscribe(f"{namespace}/#", qos=0)


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
    namespace = msg.topic.split("/", 1)[0]
    codec = next((c for c in _codecs if namespace in c.namespaces), None)
    if codec is None:
        return
    try:
        statuses = codec.decode(msg.topic, _parse(msg.payload))
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


def _fire(listeners: Sequence[m.Listener], topic: str, *args: Any) -> None:
    for listener in list(listeners):
        try:
            listener(*args)
        except Exception:
            _log.exception("mqtt: listener failed for %s", topic)
