"""The broker connection: one paho client shared by every adapter.

Inbound messages are routed by their first topic segment to the adapter that
declared that namespace; the statuses it decodes fan out to the listeners, the
external ones to the external listeners as well. Commands route by device type to
the adapter's ``encode``, and a adapter answers its own protocol through the publisher
attached at registration. The ``adapter`` provider is registered at boot; plugins register theirs during setup.
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

if TYPE_CHECKING:
    from orc.dal.interfaces import Adapter

REQUIRED_SECRETS: dict[str, Callable[[str], Any]] = {}

_log = logging.getLogger(__name__)

_MQTT_PORT = 1883

_adapters: list["Adapter"] = []
_client: mqtt.Client | None = None  # the standing client, retained for publishing commands
_listeners: list[m.Listener] = []  # run on the mqtt thread; keep them fast and don't block
_external_listeners: list[m.Listener] = []


def register(adapter: "Adapter") -> None:
    for other in _adapters:
        if taken := set(other.namespaces) & set(adapter.namespaces) or set(other.device_types) & set(adapter.device_types):
            raise ValueError(f"mqtt: {', '.join(sorted(taken))} already handled by {type(other).__name__}")
    _adapters.append(adapter)
    adapter.attach(partial(_publish, adapter))


def add_listener(fn: m.Listener) -> None:
    _listeners.append(fn)


def add_external_listener(fn: m.Listener) -> None:
    _external_listeners.append(fn)


def start() -> None:
    global _client
    _client = _new_client(orc.config.secrets, partial(_on_connect, _adapters), _on_message, 3.0)
    for adapter in _adapters:
        adapter.start()


def snapshot() -> list[m.DeviceState]:
    return [state for adapter in _adapters for state in adapter.snapshot()]


def _connected[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        if _client is None:
            raise RuntimeError(f"mqtt client not started; cannot {fn.__name__}")
        return fn(*args, **kwargs)

    return wrapper


@_connected
def command(device: m.DeviceEnum, value: Any) -> None:
    adapter = next((c for c in _adapters if device.kind in c.device_types), None)
    if adapter is None:
        raise LookupError(f"mqtt: no adapter speaks for `{device.kind}`")
    for message in adapter.encode(device, value):
        _publish(adapter, message)


def discover(adapter: "Adapter", secrets: m.Secrets, timeout: float = 3.0) -> dict[str, tuple[str, frozenset[m.Capability]]]:
    messages: list[m.Message] = []

    def on_message(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        if msg.topic.split("/", 1)[0] in adapter.namespaces:
            messages.append(m.Message(msg.topic, _parse(msg.payload), msg.retain))

    client = _new_client(secrets, partial(_on_connect, [adapter]), on_message, timeout)
    client.loop_stop()
    client.disconnect()
    return adapter.discover(messages)


def _publish(adapter: "Adapter", message: m.Message) -> None:
    assert _client is not None
    if message.topic.split("/", 1)[0] not in adapter.namespaces:
        raise ValueError(f"mqtt: {type(adapter).__name__} may not publish on {message.topic}")
    payload = json.dumps(message.payload) if isinstance(message.payload, dict) else message.payload
    _client.publish(message.topic, payload, retain=message.retain)


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


def _on_connect(adapters: Sequence["Adapter"], client: mqtt.Client, userdata: Any, flags: Any, rc: Any, *args: Any) -> None:
    if rc != 0:
        # Bad credentials land here and paho retries quietly forever; make it loud.
        _log.warning("mqtt: connect refused: %s", rc)
        return
    # Subscribe after CONNACK: paho drops (does not queue) subscriptions made earlier.
    for adapter in adapters:
        for namespace in adapter.namespaces:
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
    adapter = next((c for c in _adapters if namespace in c.namespaces), None)
    if adapter is None:
        return
    try:
        statuses = adapter.decode(msg.topic, _parse(msg.payload))
    except Exception:
        _log.exception("mqtt: message handling failed for %s", msg.topic)
        return
    for status in statuses:
        if status.source == m.Source.EXTERNAL:
            _fire(_external_listeners, msg.topic, status.device, status.attribute, status.old, status.new)
    for status in statuses:
        _fire(_listeners, msg.topic, status.device, status.attribute, status.old, status.new)


# The adapter always sees a dict: the parsed object, or {} for an empty payload (the
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
