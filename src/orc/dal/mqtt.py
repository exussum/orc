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
from datetime import datetime
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
    connected = new_client()
    if connected is None:
        _log.info("mqtt: hubitat url or MQTT_USER/MQTT_PASSWORD secrets not set, skipping")
        return
    threading.Thread(target=_run, args=connected, name="hubitat-mqtt", daemon=True).start()


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
            received=datetime.now(tz=config.tz),
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
