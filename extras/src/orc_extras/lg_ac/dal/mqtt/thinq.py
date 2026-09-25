"""ThinQ2 (clip) MQTT handling.

Background paho client connected to the local broker the AC enrolls into. Merges
the latest raw TLV values per device from ``clip/message/devices/<did>`` (decoded
on read), answers provisioning on ``clip/provisioning/devices/<did>``, and sends
commands downstream on ``lime/devices/<did>``. Modeled after orc's hubitat MQTT
subscriber: module-level client, module-level caches, listeners on the mqtt thread.

The clip transport (the upstream/downstream topics and the JSON ``packet``
envelope) is from anszom's rethink: https://github.com/anszom/rethink.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from collections.abc import Callable
from typing import Any

import paho.mqtt.client as mqtt

from orc.collections import LockedDict
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
# on_message (paho's network thread) and Flask workers (fetch_state/default_device)
# both touch this; LockedDict serializes them, and each update stores a fresh dict so
# a reader iterating a returned snapshot never races an in-place mutation. Keys are in
# first-seen order, so default_device() is the last key.
_raw: LockedDict[str, dict[int, int]] = LockedDict()  # merged latest TLV values per device
_models: LockedDict[str, str] = LockedDict()  # device id -> model kind from its preDeploy payload
_raw_listeners: list[Callable[[str, bytes], None]] = []  # every inbound message, undecoded
_event_listener: Callable[[str, str], None] | None = None  # major events (pairing, state changes, dropped commands)


def add_raw_listener(fn: Callable[[str, bytes], None]) -> None:
    _raw_listeners.append(fn)


def set_event_listener(fn: Callable[[str, str], None]) -> None:
    global _event_listener
    _event_listener = fn


def event(device_id: str, msg: str) -> None:
    if _event_listener is not None:
        _event_listener(device_id, f"AC {device_id[:8]}: {msg}")


def _seen(device_id: str) -> None:
    _raw.update(device_id, lambda cur: cur if cur is not None else {})


def start(host: str, port: int = 1883, username: str | None = None, password: str | None = None, clip_ids: list[str] | None = None) -> None:
    global _client
    # clip_ids ride paho's per-client userdata, delivered to _on_connect on every
    # (re)connect so it can nudge the configured ACs — no module-level state needed.
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="lg_ac", userdata=clip_ids or [])
    if username is not None:
        client.username_pw_set(username, password)
    client.on_connect = _on_connect
    client.on_message = _on_message
    client.on_disconnect = _on_disconnect
    client.reconnect_delay_set(min_delay=1, max_delay=60)
    client.connect_async(host, port, keepalive=30)
    client.loop_start()
    _client = client


def _fieldmap(device_id: str) -> m.Fieldmap | None:
    model = _models.get(device_id)
    return api.load_fieldmap(model) if model else None


def fetch_state(device_id: str) -> m.ACState:
    fm = _fieldmap(device_id)
    if fm is None:
        return m.ACState()
    return api.state_from_raw(fm, _raw.get(device_id) or {})


def devices() -> list[str]:
    return list(_raw.copy())


def default_device() -> str | None:
    return next(reversed(_raw.copy()), None)


def _envelope(device_id: str, cmd: str, type_: int, data: str) -> bytes:
    return json.dumps({"did": device_id, "mid": int(time.time() * 1000), "cmd": cmd, "type": type_, "data": data}).encode()


def _send_packet(device_id: str, frame: bytes, retain: bool = False) -> None:
    if _client is None:
        return
    _client.publish(_DOWNSTREAM_PREFIX + device_id, _envelope(device_id, "packet", 1, frame.hex()), retain=retain)


def _nudge(device_id: str) -> None:
    # Retained so a unit that reconnects after an orc restart is prompted on its next
    # subscribe, instead of sitting idle (invisible to devices()/commands) until a manual
    # power-cycle re-provisions it. Diverges from rethink, which never queries and waits
    # for the device to push state.
    _send_packet(device_id, api.build_query(api.Query.VALUES), retain=True)


def publish_command(device_id: str, values: dict[str, object]) -> None:
    if _client is None:
        raise RuntimeError("mqtt client not started; cannot command device")
    fm = _fieldmap(device_id)
    if fm is None:
        event(device_id, "command dropped, no field map")
        return
    _send_packet(device_id, api.build_command(fm, values))


def _on_connect(client: mqtt.Client, userdata: Any, flags: Any, rc: Any, *args: Any) -> None:
    failed = getattr(rc, "is_failure", None)
    if failed is None:
        failed = rc != 0
    if failed:
        _log.warning("mqtt connect failed: rc=%s", rc)
        return
    client.subscribe("#", qos=0)
    for clip_id in userdata:
        _nudge(clip_id)


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
        _seen(device_id)
        _poll(device_id)
    elif cmd == "device_packet":
        fm = _fieldmap(device_id)
        if fm is None:
            return  # unknown model: skip decode (enable capture to log raw frames for calibration)
        pkt = api.frame_tlv(bytes.fromhex(msg.get("data", "")))
        if pkt is None:
            return
        values = {f.type_id: f.value for f in pkt.fields}
        old = _raw.get(device_id) or {}
        _raw.update(device_id, lambda cur: {**(cur or {}), **values})
        _event_state_changes(fm, device_id, old, {**old, **values})
    elif cmd == "req_timesync":
        _send_timesync(device_id)


# current_temperature is deliberately excluded: it drifts constantly and would flood the log
def _event_state_changes(fm: m.Fieldmap, device_id: str, old: dict[int, int], new: dict[int, int]) -> None:
    before = api.state_from_raw(fm, old)
    after = api.state_from_raw(fm, new)
    changes = [
        f"{field} {b} → {a}"
        for field, b, a in zip(before._fields, before, after, strict=True)
        if field != "current_temperature" and b is not None and b != a
    ]
    if changes:
        event(device_id, ", ".join(changes))


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
    if model and model != _models.get(device_id):
        _models.update(device_id, lambda cur: model)
        if api.load_fieldmap(model) is None:
            _log.warning("no field map for model %s; capture-only until one exists", model)
            event(device_id, f"no field map for model {model}; capture-only")
    _seen(device_id)
    if _client is not None:
        response = api.deploy(device_id, int(time.time() * 1000), device_cmd)
        _client.publish(_DOWNSTREAM_PREFIX + device_id, json.dumps(response).encode())


def _poll(device_id: str) -> None:
    if _client is None:
        return
    _send_packet(device_id, api.build_query(api.Query.CAPABILITIES))
    _send_packet(device_id, api.build_query(api.Query.VALUES))
