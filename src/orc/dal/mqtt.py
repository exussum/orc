"""Hubitat MQTT Export subscriber.

Background paho client that caches the retained per-device JSON documents the
hub publishes to ``hubitat/<hub-uuid>/devices/<id>``. Read-only: state and
events come from here in later parts; commands and discovery stay on Maker API.
The hub uuid is captured from the first message rather than configured.
"""

import dataclasses
import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import paho.mqtt.client as mqtt

from orc import config
from orc.collections import LockedDict

_log = logging.getLogger(__name__)

_MQTT_PORT = 1883


@dataclasses.dataclass(frozen=True)
class DeviceState:
    id: int
    name: str
    attributes: dict[str, Any]
    last_activity: str | None
    received: datetime


_devices: LockedDict[int, DeviceState] = LockedDict()
_hub_id: str | None = None  # written only by the mqtt thread


def start() -> None:
    host = _mqtt_host()
    user = config.secrets.get("MQTT_USER")
    password = config.secrets.get("MQTT_PASSWORD")
    if not host:
        _log.info("mqtt: no hubitat url configured, skipping")
        return
    if not (user and password):
        _log.info("mqtt: MQTT_USER/MQTT_PASSWORD secrets not set, skipping")
        return
    threading.Thread(target=_run, args=(host, user, password), name="hubitat-mqtt", daemon=True).start()


def hub_id() -> str | None:
    return _hub_id


def snapshot() -> list[DeviceState]:
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
        device = DeviceState(
            id=int(doc["id"]),
            name=doc["name"],
            attributes={a["name"]: a["value"] for a in doc["attributes"]},
            last_activity=doc.get("lastActivity"),
            received=datetime.now(tz=config.tz),
        )
    except ValueError, KeyError, TypeError:
        _log.exception("mqtt: bad device document on %s", msg.topic)
        return
    _devices[device.id] = device


def _run(host: str, user: str, password: str) -> None:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(user, password)
    client.on_connect = _on_connect
    client.on_message = _on_message
    client.reconnect_delay_set(min_delay=1, max_delay=60)
    while True:
        try:
            client.connect(host, _MQTT_PORT, keepalive=30)
        except Exception:
            _log.exception("mqtt: connect failed; retrying in 60s")
            time.sleep(60)
            continue
        client.loop_forever()  # reconnects internally; returns only if stopped
        time.sleep(1)
