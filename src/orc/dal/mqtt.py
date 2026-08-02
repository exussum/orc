"""Hubitat MQTT Export subscriber.

Background paho client that caches the retained per-device JSON documents the
hub publishes to ``hubitat/<hub-uuid>/devices/<id>``. State, events, commands,
and discovery all flow through here; only hub reboot stays on Maker API.
The hub uuid is captured from the first message rather than configured.
"""

import json
import logging
import os
import time
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import urlsplit

import paho.mqtt.client as mqtt

from orc import config
from orc import model as m
from orc.collections import LockedDict
from orc.dal import sqlite
from orc.decorators import requires_enabled

_log = logging.getLogger(__name__)

_MQTT_PORT = 1883


_devices: LockedDict[int, m.DeviceState] = LockedDict()
_hub_id: str | None = None  # written only by the mqtt thread
_client: mqtt.Client | None = None  # the standing client, retained for publishing commands

# Command round-trip measurement: publish_light stamps the command topic at send, and
# the broker echoing our own publish back on the hubitat/# subscription pops it. Keyed
# by topic, so the size is bounded by distinct (device, command) pairs; an entry whose
# echo never arrives (broker down at publish) is overwritten by the topic's next send.
# Round trips fold into orc_durations, keyed by command topic.
_command_sent: LockedDict[str, float] = LockedDict()  # command topic -> monotonic send time

# Central event listeners: fired as (device, attribute, old, new) for every attribute
# of every received document that represents something happening — battery levels,
# motion active/inactive, contact open/close, switch state, all attributes alike.
# State is never an event: first sightings (the retained flood at boot) and replays
# (a document identical to the cached one — reconnect floods, hub republish after
# reboot) update the cache but fire no listeners, so ``old`` is never None. No other
# dedup: the hub regenerates the document only when something happens, so a changed
# document fires every attribute, unchanged ones included (old == new); consumers
# filter for what they care about. Callbacks run on the mqtt thread; keep them fast
# and don't block.
type Listener = Callable[[m.DeviceState, str, Any, Any], None]
_listeners: list[Listener] = []

# Button-event listeners: fired as (device id, button number, event type) for every
# ``devices/<id>/button/<n>`` publish. Unlike the document topics these are dedicated
# event messages (not retained, no flood replay), so no staleness filtering applies.
type ButtonListener = Callable[[int, int, str], None]
_button_listeners: list[ButtonListener] = []


def add_listener(fn: Listener) -> None:
    _listeners.append(fn)


def add_button_listener(fn: ButtonListener) -> None:
    _button_listeners.append(fn)


def _new_client(secrets: m.Secrets, on_connect: Callable[..., None], on_message: Callable[..., None], timeout: float) -> mqtt.Client | None:
    """A credentialed, connected client wired to the given callbacks, its network loop
    running on a paho background thread, returned once the retained flood settles or
    ``timeout`` passes (logged). The flood is watched through a boot-only wrapper
    counting retained messages (the broker sets the RETAIN flag only on subscription
    replay, never on live forwards); the raw handler is restored before returning.
    None when the host or MQTT_USER/MQTT_PASSWORD secrets are missing (logged)."""
    host = _mqtt_host()
    user, password = secrets.get("MQTT_USER"), secrets.get("MQTT_PASSWORD")
    if not (host and user and password):
        _log.warning("mqtt: host or MQTT_USER/MQTT_PASSWORD missing, skipping")
        return None

    received = 0

    def counting(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        nonlocal received
        received += msg.retain
        on_message(client, userdata, msg)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(user, password)
    client.on_connect = on_connect
    client.on_message = counting
    client.reconnect_delay_set(min_delay=1, max_delay=60)
    client.connect_async(host, _MQTT_PORT, keepalive=30)
    client.loop_start()
    if not _wait_settled(lambda: received, timeout):
        _log.warning("mqtt: retained documents still arriving after %.0fs (%d so far)", timeout, received)
    client.on_message = on_message
    return client


def start() -> None:
    global _client
    _client = _new_client(config.secrets, _on_connect, _on_message, 3.0)


def snapshot() -> list[m.DeviceState]:
    return sorted(_devices.values(), key=lambda d: d.id)


@requires_enabled(lambda lights: m.Configs(*(m.Config(what=light, state=m.OFF) for light in lights)))
def fetch_light_states(lights: Sequence[m.DeviceEnum]) -> m.Configs:
    """Light states from the standing subscriber's device documents (updated on every
    device event, whatever channel commanded it). Virtual devices (negative synthetic
    id), devices not selected in the MQTT Export app, and an unpopulated cache (broker
    down / just booted) report off, matching the old poll's missing-device rule."""
    found = {d.id: d.attributes for d in snapshot()}

    def state(light: m.DeviceEnum) -> int | str:
        attrs = found.get(light.value)
        if attrs is None:
            return m.OFF
        switch = attrs.get("switch", m.OFF)
        return int(attrs["level"]) if ("level" in attrs and switch == m.ON) else switch

    return m.Configs(*(m.Config(what=light, state=state(light)) for light in lights))


@requires_enabled({})
def fetch_hubitat_config(secrets: m.Secrets, timeout: float = 3.0) -> dict[str, tuple[int, frozenset[m.Capability]]]:
    found: dict[str, tuple[int, frozenset[m.Capability]]] = {}

    def on_message(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        device = _parse_device_state(msg)
        if device is not None:
            dimmable = "level" in device.attributes
            found[device.name] = (device.id, frozenset([m.Capability.change_level]) if dimmable else frozenset())

    client = _new_client(secrets, _on_connect, on_message, timeout)
    if client is not None:
        client.loop_stop()
        client.disconnect()
    if not found:
        raise RuntimeError("mqtt: device discovery found no device documents; broker unreachable or MQTT credentials missing")
    return found


@requires_enabled(None)
def publish_light(light: m.DeviceEnum, on: bool | None = None, brightness: int | None = None) -> None:
    if brightness is not None and m.Capability.change_level in light.capabilities:
        command, payload = "setLevel", str(brightness)
    else:
        if brightness == 0:
            on = False
        elif brightness == 100:
            on = True
        elif brightness is not None:
            raise ValueError(f"{light.name} does not support ChangeLevel; cannot set brightness {brightness}")
        command, payload = (m.ON if on else m.OFF), None
    if _client is None or _hub_id is None:
        raise RuntimeError(f"mqtt client not started or hub not yet seen; cannot command {light.name}")
    topic = f"hubitat/{_hub_id}/devices/{light.value}/commands/{command}"
    _command_sent[topic] = time.monotonic()
    _client.publish(topic, payload)


def _mqtt_host() -> str | None:
    return os.getenv("ORC_MQTT_HOST") or urlsplit(config.hubitat_url or "").hostname


def _on_connect(client: mqtt.Client, userdata: Any, flags: Any, rc: Any, *args: Any) -> None:
    if rc != 0:
        # Bad credentials land here and paho retries quietly forever; make it loud.
        _log.warning("mqtt: connect refused: %s", rc)
        return
    # Subscribe after CONNACK: paho drops (does not queue) subscriptions made earlier.
    client.subscribe("hubitat/#", qos=0)


def _parse_device_state(msg: mqtt.MQTTMessage) -> m.DeviceState | None:
    parts = msg.topic.split("/")
    if len(parts) != 4 or parts[2] != "devices":
        return None
    try:
        doc = json.loads(msg.payload)
        return m.DeviceState(
            id=int(doc["id"]),
            name=doc["name"],
            attributes={a["name"]: a["value"] for a in doc["attributes"]},
            last_activity=doc.get("lastActivity"),
        )
    except ValueError, KeyError, TypeError:
        _log.exception("mqtt: bad device document on %s", msg.topic)
        return None


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
    global _hub_id
    parts = msg.topic.split("/")
    if len(parts) < 4 or parts[2] != "devices":
        return  # location/variables topics
    _hub_id = parts[1]
    kind = parts[4] if len(parts) > 4 else None
    if kind is None:
        _receive_document(msg)
    elif kind == "button" and msg.payload:  # the empty clearing publish follows each event
        _receive_button_event(msg)
    elif kind == "commands":
        _receive_command_echo(msg)


def _receive_document(msg: mqtt.MQTTMessage) -> None:
    device = _parse_device_state(msg)
    if device is None:
        return
    old, _devices[device.id] = _devices.get(device.id), device
    if old is None or old == device:
        return
    for attribute, new_value in device.attributes.items():
        _fire(_listeners, msg.topic, device, attribute, old.attributes.get(attribute), new_value)


def _receive_command_echo(msg: mqtt.MQTTMessage) -> None:
    sent = _command_sent.pop(msg.topic)
    if sent is not None:
        sqlite.update_avg(msg.topic, time.monotonic() - sent)


def _receive_button_event(msg: mqtt.MQTTMessage) -> None:
    try:
        doc = json.loads(msg.payload)
        event = int(msg.topic.split("/")[3]), int(doc["button"]), doc["event_type"]
    except ValueError, KeyError, TypeError:
        _log.exception("mqtt: bad button event on %s", msg.topic)
        return
    _fire(_button_listeners, msg.topic, *event)


def _fire(listeners: Sequence[Callable[..., None]], topic: str, *args: Any) -> None:
    """Call each listener, isolating failures so one bad consumer can't starve the
    rest or kill the mqtt thread."""
    for listener in list(listeners):
        try:
            listener(*args)
        except Exception:
            _log.exception("mqtt: listener failed for %s", topic)
