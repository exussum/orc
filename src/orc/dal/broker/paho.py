"""The broker connection: one paho client shared by every adapter."""

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
from orc.dal import broker

if TYPE_CHECKING:
    from orc.dal.interfaces import Adapter

_log = logging.getLogger(__name__)

REQUIRED_SECRETS: dict[str, Callable[[str], Any]] = {}

_MQTT_PORT = 1883

_client: mqtt.Client | None = None  # the standing client, retained for publishing commands


def register(adapter: "Adapter") -> None:
    broker.register(adapter, partial(_publish, adapter))


def start() -> None:
    global _client
    if any(adapter.namespaces for adapter in broker.adapters):
        _client = _new_client(orc.config.secrets, partial(_on_connect, broker.adapters), _on_message, 3.0)
    for adapter in broker.adapters:
        adapter.start()


def _connected[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        if _client is None:
            raise RuntimeError(f"mqtt client not started; cannot {fn.__name__}")
        return fn(*args, **kwargs)

    return wrapper


@_connected
def command(device: m.DeviceEnum, value: Any) -> None:
    adapter = broker.adapter_for(device)
    for message in adapter.encode(device, value):
        _publish(adapter, message)


def discover(adapter: "Adapter", secrets: m.Secrets, timeout: float = 3.0) -> dict[str, tuple[str, frozenset[m.Capability]]]:
    if not adapter.namespaces:
        return adapter.discover(())
    messages: list[m.Message] = []

    def on_message(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        if msg.topic.split("/", 1)[0] in adapter.namespaces:
            messages.append(m.Message(msg.topic, broker.parse(msg.payload), msg.retain))

    client = _new_client(secrets, partial(_on_connect, [adapter]), on_message, timeout)
    client.loop_stop()
    client.disconnect()
    return adapter.discover(messages)


def _publish(adapter: "Adapter", message: m.Message) -> None:
    assert _client is not None
    if message.topic.split("/", 1)[0] not in adapter.namespaces:
        raise ValueError(f"mqtt: {type(adapter).__name__} may not publish on {message.topic}")
    payload = json.dumps(message.payload) if isinstance(message.payload, dict) else message.payload
    info = _client.publish(message.topic, payload, retain=message.retain)
    if info.rc != mqtt.MQTT_ERR_SUCCESS:
        raise ConnectionError(f"mqtt: publish on {message.topic} failed: {mqtt.error_string(info.rc)}")


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
        raise ConnectionRefusedError(f"mqtt: connect refused: {rc}")
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
    broker.receive(msg.topic, msg.payload)
