"""Hubitat MQTT Export subscriber.

Background paho client that caches the retained per-device JSON documents the
hub publishes to ``hubitat/<hub-uuid>/devices/<id>``. Read-only: state and
events come from here in later parts; commands and discovery stay on Maker API.
The hub uuid is captured from the first message rather than configured.
"""

import json
import logging
import os
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import urlsplit

import paho.mqtt.client as mqtt

from orc import config
from orc import model as m
from orc.collections import LockedDict
from orc.decorators import requires_enabled

_log = logging.getLogger(__name__)

_MQTT_PORT = 1883


_devices: LockedDict[int, m.DeviceState] = LockedDict()
_hub_id: str | None = None  # written only by the mqtt thread
_client: mqtt.Client | None = None  # the standing client, retained for publishing commands

# Central event listeners: fired as (device, attribute, old, new) for every attribute
# of every received document — battery levels, motion active/inactive, contact
# open/close, switch state, all attributes alike. No dedup: the hub republishes the
# whole document when anything changes, so unchanged attributes fire too (old == new),
# and the initial retained flood fires with old=None. Consumers filter for what they
# care about. Callbacks run on the mqtt thread; keep them fast and don't block.
type Listener = Callable[[m.DeviceState, str, Any, Any], None]
_listeners: list[Listener] = []


def add_listener(fn: Listener) -> None:
    if fn not in _listeners:
        _listeners.append(fn)


def new_client() -> tuple[mqtt.Client, str, int] | None:
    """A credentialed client plus broker host/port, or None when the hubitat url or
    MQTT_USER/MQTT_PASSWORD secrets are missing."""
    host = _mqtt_host()
    user = config.secrets.get("MQTT_USER")
    password = config.secrets.get("MQTT_PASSWORD")
    if not (host and user and password):
        return None
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(user, password)
    return client, host, _MQTT_PORT


def start() -> None:
    global _client
    connected = new_client()
    if connected is None:
        _log.info("mqtt: hubitat url or MQTT_USER/MQTT_PASSWORD secrets not set, skipping")
        return
    _client = connected[0]
    threading.Thread(target=_run, args=connected, name="hubitat-mqtt", daemon=True).start()
    # Block boot briefly until the retained flood settles (device count nonzero and
    # stable), so early reads don't see an empty cache and report every light off.
    deadline = time.monotonic() + 3.0
    previous = -1
    while time.monotonic() < deadline:
        time.sleep(0.25)
        count = len(_devices.values())
        if count and count == previous:
            return
        previous = count
    _log.warning("mqtt: retained documents still arriving after 3s (%d so far)", max(previous, 0))


def hub_id() -> str | None:
    return _hub_id


def snapshot() -> list[m.DeviceState]:
    return sorted(_devices.values(), key=lambda d: d.id)


def state_rows() -> list[dict[str, Any]]:
    """Per-device rows for core's generic state renderer (needs a "name" key)."""
    return [
        {
            "name": d.name,
            "id": d.id,
            "attributes": ", ".join(f"{k}={v}" for k, v in sorted(d.attributes.items())),
            "last_activity": d.last_activity,
        }
        for d in snapshot()
    ]


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
    """One-shot boot-time discovery from the retained device documents: name ->
    (id, capabilities). Runs during config load — before ``config.secrets`` exists and
    before the standing client starts — so credentials come in explicitly. Dimmable is
    inferred from a ``level`` attribute in the document (verified equivalent to Maker
    API's ChangeLevel capability for every exported device)."""
    host = _mqtt_host()
    user, password = secrets.get("MQTT_USER"), secrets.get("MQTT_PASSWORD")
    if not (host and user and password):
        _log.warning("mqtt: host or MQTT_USER/MQTT_PASSWORD missing; no devices discovered")
        return {}
    found: dict[str, tuple[int, frozenset[m.Capability]]] = {}

    def on_connect(client: mqtt.Client, userdata: Any, flags: Any, rc: Any, *args: Any) -> None:
        if rc != 0:
            _log.warning("mqtt: discovery connect refused: %s", rc)
        else:
            client.subscribe("hubitat/#", qos=0)

    def on_message(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        parts = msg.topic.split("/")
        if len(parts) != 4 or parts[2] != "devices":
            return
        try:
            doc = json.loads(msg.payload)
            dimmable = any(a["name"] == "level" for a in doc["attributes"])
            found[doc["name"]] = (int(doc["id"]), frozenset([m.Capability.change_level]) if dimmable else frozenset())
        except ValueError, KeyError, TypeError:
            _log.exception("mqtt: bad device document on %s", msg.topic)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(user, password)
    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect(host, _MQTT_PORT, keepalive=30)
    except OSError:
        _log.warning("mqtt: discovery connect failed; no devices discovered")
        return {}
    client.loop_start()
    # Collect the retained flood until the device count settles, like start()'s wait.
    deadline = time.monotonic() + timeout
    previous = -1
    while time.monotonic() < deadline:
        time.sleep(0.25)
        if found and len(found) == previous:
            break
        previous = len(found)
    client.loop_stop()
    client.disconnect()
    return found


@requires_enabled(None)
def publish_light(light: m.DeviceEnum, on: bool | None = None, brightness: int | None = None) -> None:
    """Command a light by publishing to its command topic. The broker accepting the
    publish is the only acknowledgement; the device's state document is the signal
    that a command actually landed. setLevel takes the raw value as payload; the
    driver treats level 0 as off (verified live)."""
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
    _client.publish(f"hubitat/{_hub_id}/devices/{light.value}/commands/{command}", payload)


def _mqtt_host() -> str | None:
    return os.getenv("ORC_MQTT_HOST") or urlsplit(config.hubitat_url or "").hostname


def _on_connect(client: mqtt.Client, userdata: Any, flags: Any, rc: Any, *args: Any) -> None:
    if rc != 0:
        # Bad credentials land here and paho retries quietly forever; make it loud.
        _log.warning("mqtt: connect refused: %s", rc)
        return
    # Subscribe after CONNACK: paho drops (does not queue) subscriptions made earlier.
    client.subscribe("hubitat/#", qos=0)


def _on_message(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
    global _hub_id
    parts = msg.topic.split("/")
    if len(parts) != 4 or parts[2] != "devices":
        return  # location/variables topics, or command echoes
    _hub_id = parts[1]
    try:
        doc = json.loads(msg.payload)
        device = m.DeviceState(
            id=int(doc["id"]),
            name=doc["name"],
            attributes={a["name"]: a["value"] for a in doc["attributes"]},
            last_activity=doc.get("lastActivity"),
        )
    except ValueError, KeyError, TypeError:
        _log.exception("mqtt: bad device document on %s", msg.topic)
        return
    old = _devices.get(device.id)
    _devices[device.id] = device
    for attribute, new_value in device.attributes.items():
        old_value = old.attributes.get(attribute) if old is not None else None
        for listener in list(_listeners):
            try:
                listener(device, attribute, old_value, new_value)
            except Exception:
                _log.exception("mqtt: listener failed for %s.%s", device.name, attribute)


def _run(client: mqtt.Client, host: str, port: int) -> None:
    client.on_connect = _on_connect
    client.on_message = _on_message
    client.reconnect_delay_set(min_delay=1, max_delay=60)
    while True:
        try:
            client.connect(host, port, keepalive=30)
        except Exception:
            _log.exception("mqtt: connect failed; retrying in 60s")
            time.sleep(60)
            continue
        client.loop_forever()  # reconnects internally; returns only if stopped
        time.sleep(1)
