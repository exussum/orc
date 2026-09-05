"""ThinQ2 (clip) MQTT handling.

Background paho client connected to the local broker the AC enrolls into. Caches
the latest decoded state per device id from ``clip/message/devices/<did>``,
answers provisioning requests on ``clip/provisioning/devices/<did>``, and
publishes commands back on the standing client. Modeled after orc's hubitat MQTT
subscriber: module-level client, module-level caches, listeners fired on the mqtt
thread.
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import paho.mqtt.client as mqtt

from orc_extras.lg_ac import api
from orc_extras.lg_ac import model as m

_log = logging.getLogger(__name__)

# The device publishes upstream to clip/message and clip/provisioning, but it
# subscribes to a firmware-baked-in downstream topic (lime/devices/<did>) and
# ignores the topics we advertise. All server->device traffic goes there, as a
# JSON envelope {cmd:"packet", type:1, data:<hex aabb frame>} — not raw binary.
_MESSAGE_PREFIX = "clip/message/devices/"
_PROVISIONING_PREFIX = "clip/provisioning/devices/"
_DOWNSTREAM_PREFIX = "lime/devices/"

_client: mqtt.Client | None = None  # standing client, retained for publishing commands
_lock = threading.Lock()
_raw: dict[str, dict[int, int]] = {}  # merged latest TLV values per device (frames are partial)
_devices: list[str] = []  # device ids seen, in first-seen order
_raw_listeners: list[Callable[[str, bytes], None]] = []  # every clip message, undecoded


def add_raw_listener(fn: Callable[[str, bytes], None]) -> None:
    _raw_listeners.append(fn)


def start(host: str, port: int = 1883, username: str | None = None, password: str | None = None) -> None:
    global _client
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="lg_ac")
    if username is not None:
        client.username_pw_set(username, password)
    client.on_connect = _on_connect
    client.on_message = _on_message
    client.on_disconnect = _on_disconnect
    client.reconnect_delay_set(min_delay=1, max_delay=60)
    client.connect_async(host, port, keepalive=30)
    client.loop_start()
    _client = client


def fetch_state(device_id: str) -> m.ACState:
    with _lock:
        return api.state_from_raw(_raw.get(device_id, {}))


def devices() -> list[str]:
    with _lock:
        return list(_devices)


def default_device() -> str | None:
    with _lock:
        return _devices[-1] if _devices else None


def _envelope(device_id: str, cmd: str, type_: int, data: str) -> bytes:
    return json.dumps({"did": device_id, "mid": int(time.time() * 1000), "cmd": cmd, "type": type_, "data": data}).encode()


def _send_packet(device_id: str, frame: bytes) -> None:
    if _client is None:
        return
    _client.publish(_DOWNSTREAM_PREFIX + device_id, _envelope(device_id, "packet", 1, frame.hex()))


def publish_command(device_id: str, values: dict[str, object]) -> None:
    if _client is None:
        raise RuntimeError("mqtt client not started; cannot command device")
    _send_packet(device_id, api.build_command(values))


def _on_connect(client: mqtt.Client, userdata: Any, flags: Any, rc: Any, *args: Any) -> None:
    failed = getattr(rc, "is_failure", None)
    if failed is None:
        failed = rc != 0
    if failed:
        _log.warning("mqtt connect failed: rc=%s", rc)
        return
    client.subscribe("#", qos=0)


def _on_disconnect(client: mqtt.Client, userdata: Any, *args: Any) -> None:
    _log.warning("mqtt client disconnected")


def _on_message(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
    for listener in list(_raw_listeners):
        try:
            listener(msg.topic, msg.payload)
        except Exception:
            _log.exception("raw listener failed for %s", msg.topic)
    if msg.topic.startswith(_MESSAGE_PREFIX):
        _receive_message(msg.topic, msg.payload)
    elif msg.topic.startswith(_PROVISIONING_PREFIX):
        _receive_provisioning(msg.topic, msg.payload)


def _receive_message(topic: str, payload: bytes) -> None:
    device_id = topic[len(_MESSAGE_PREFIX) :]
    try:
        msg = json.loads(payload.rstrip(b"\x00"))  # device appends a null terminator
    except ValueError:
        return
    cmd = msg.get("cmd")
    if cmd == "completeProvisioning_ack":
        with _lock:
            if device_id not in _devices:
                _devices.append(device_id)
        _poll(device_id)
    elif cmd == "device_packet":
        if api.active_model() is None:
            return  # unknown model: raw frame is still in capture.jsonl, just not decoded
        pkt = api.frame_tlv(bytes.fromhex(msg.get("data", "")))
        if pkt is None:
            return
        with _lock:
            merged = _raw.setdefault(device_id, {})
            merged.update({f.type_id: f.value for f in pkt.fields})
            if device_id not in _devices:
                _devices.append(device_id)
    elif cmd == "req_timesync":
        _send_timesync(device_id)


def _send_timesync(device_id: str) -> None:
    if _client is None:
        return
    now = time.gmtime()
    buf = bytes([now.tm_year % 100, now.tm_mon - 1, now.tm_mday, now.tm_hour, now.tm_min, now.tm_sec, (now.tm_wday + 1) % 7])
    _client.publish(_DOWNSTREAM_PREFIX + device_id, _envelope(device_id, "resp_timesync", 1, base64.b64encode(buf).decode()))


def _receive_provisioning(topic: str, payload: bytes) -> None:
    device_id = topic[len(_PROVISIONING_PREFIX) :]
    try:
        incoming = json.loads(payload.rstrip(b"\x00"))
    except ValueError:
        return
    device_cmd = incoming.get("cmd")
    if device_cmd not in ("preDeploy", "deploy"):
        return  # ignore our own completeProvisioning response echoed back
    model = incoming.get("kind")
    if model and api.active_model() != model and not api.select_model(model):
        _log.warning("no field map for model %s; capture-only until one exists", model)
    with _lock:
        if device_id not in _devices:
            _devices.append(device_id)
    if _client is not None:
        response = api.deploy(device_id, int(time.time() * 1000), device_cmd)
        _client.publish(_DOWNSTREAM_PREFIX + device_id, json.dumps(response).encode())


def _poll(device_id: str) -> None:
    if _client is None:
        return
    _send_packet(device_id, api.build_query(caps=True))
    _send_packet(device_id, api.build_query(caps=False))
