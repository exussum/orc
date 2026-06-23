import dataclasses
import json
import logging
import os
import signal
import threading
import time
import uuid
from datetime import datetime

import paho.mqtt.client as mqtt
import requests

import orc as config
from orc._locked_dict import LockedDict
from orc.model import SensorState

_AUTH_URL = "https://api.yosmart.com/open/yolink/token"
_API_URL = "https://api.yosmart.com/open/yolink/v2/api"
_MQTT_HOST = "api.yosmart.com"
_MQTT_PORT = 8003

STATE_DRY = "normal"
STATE_WET = "alert"

_log = logging.getLogger(__name__)

_states = LockedDict()  # device_id -> SensorState
_on_transition = None  # callback (name, kind, old, new); kind in {"connection", "leak"}

# Flap suppression: paho auto-reconnects transient drops, so only treat the
# connection as down once we've seen several disconnects in a short window.
_FLAP_WINDOW_SEC = 60
_FLAP_THRESHOLD = 3
_disconnect_times: list[float] = []

_TRACKED_FIELDS = (
    ("state", "leak"),
    ("battery", "battery"),
    ("signal", "signal"),
    ("interval", "interval"),
    ("online", "online"),
)


def set_transition_callback(fn):
    global _on_transition
    _on_transition = fn


def snapshot():
    sensors = _states.copy()
    return [sensors.get(device.value) or SensorState(name=device.name, device_id=device.value) for device in config.Leak]


def _transition_to(new_state, require=None):
    def fn(current):
        if current is None or (require is not None and current.state != require):
            return None
        return dataclasses.replace(current, state=new_state, last_change=datetime.now(tz=config.config.tz))

    return fn


def simulate_transition(name: str):
    sensor = next((s for s in _states.copy().values() if s.name == name), None)
    if sensor is None:
        return False
    device_id = sensor.device_id
    prev = sensor.state if sensor.state in (STATE_DRY, STATE_WET) else STATE_DRY

    if _states.update(device_id, _transition_to(STATE_WET)) is None:
        return False
    _fire("leak", name, prev, STATE_WET)

    def _revert():
        time.sleep(5)
        if _states.update(device_id, _transition_to(STATE_DRY, require=STATE_WET)) is not None:
            _fire("leak", name, STATE_WET, STATE_DRY)

    threading.Thread(target=_revert, name=f"yolink-test-revert-{name}", daemon=True).start()
    return True


def _fire(kind, name, old, new):
    if _on_transition and old != new:
        try:
            _on_transition(name, kind, old, new)
        except Exception:
            _log.exception("yolink transition callback failed")


def _authenticate():
    response = requests.post(
        _AUTH_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": config.config.secrets.yolink_id,
            "client_secret": config.config.secrets.yolink_secret,
        },
        timeout=config.config.http_timeout,
    )
    response.raise_for_status()
    body = response.json()
    return body["access_token"], int(body.get("expires_in", 7200))


def _fetch_home_id(access_token):
    response = requests.post(
        _API_URL,
        json={"method": "Home.getGeneralInfo"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=config.config.http_timeout,
    )
    response.raise_for_status()
    return response.json()["data"]["id"]


def _fetch_device_tokens(access_token):
    response = requests.post(
        _API_URL,
        json={"method": "Home.getDeviceList"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=config.config.http_timeout,
    )
    response.raise_for_status()
    return {d["deviceId"]: d["token"] for d in response.json()["data"]["devices"]}


def _fetch_leak_state(access_token, device_id, device_token):
    response = requests.post(
        _API_URL,
        json={"method": "LeakSensor.getState", "targetDevice": device_id, "token": device_token},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=config.config.http_timeout,
    )
    response.raise_for_status()
    return response.json()["data"]


def _update_sensor(device_id, **fields):
    _states.update(device_id, lambda current: dataclasses.replace(current, **fields) if current else None)


def _hydrate_states(access_token):
    tokens = _fetch_device_tokens(access_token)
    for device in config.Leak:
        device_token = tokens.get(device.value)
        if device_token is None:
            continue
        try:
            data = _fetch_leak_state(access_token, device.value, device_token)
        except Exception:
            _log.exception("yolink: getState failed for %s", device.name)
            continue
        state = data.get("state") or {}
        _update_sensor(
            device.value,
            online=data.get("online"),
            state=state.get("state"),
            battery=state.get("battery"),
            interval=state.get("interval"),
            signal=(state.get("loraInfo") or {}).get("signal"),
        )


def _on_message(client, userdata, msg):
    parts = msg.topic.split("/")
    if len(parts) < 4 or parts[3] != "report":
        return
    device_id = parts[2]
    try:
        payload = json.loads(msg.payload.decode())
    except Exception:
        _log.exception("yolink: bad payload on %s", msg.topic)
        return
    data = payload.get("data") or {}

    captured = {"name": None, "transitions": []}

    def apply(current):
        if current is None:
            return None
        changes = {}
        for field_name, kind in _TRACKED_FIELDS:
            new = data.get(field_name)
            if new is None:
                continue
            old = getattr(current, field_name)
            if old == new:
                continue
            changes[field_name] = new
            captured["transitions"].append((kind, old, new))
        if not changes:
            return None
        captured["name"] = current.name
        return dataclasses.replace(current, last_change=datetime.now(tz=config.config.tz), **changes)

    _states.update(device_id, apply)
    for kind, old, new in captured["transitions"]:
        _fire(kind, captured["name"], old, new)


def _set_connected(connected):
    fired_names = []

    def apply(current):
        if current is None or current.connected == connected:
            return None
        fired_names.append(current.name)
        return dataclasses.replace(current, connected=connected)

    for device_id in _states.copy().keys():
        _states.update(device_id, apply)
    for name in fired_names:
        _fire("connection", name, None, "connected" if connected else "disconnected")


def _on_connect(client, userdata, flags, rc, *args):
    if rc != 0:
        _log.warning("yolink: mqtt connect rc=%s", rc)
        return
    home_id = userdata["home_id"]
    client.subscribe(f"yl-home/{home_id}/+/report", qos=0)
    _set_connected(True)


def _on_disconnect(client, userdata, *args):
    now = time.time()
    cutoff = now - _FLAP_WINDOW_SEC
    _disconnect_times[:] = [t for t in _disconnect_times if t >= cutoff]
    _disconnect_times.append(now)
    if len(_disconnect_times) >= _FLAP_THRESHOLD:
        _set_connected(False)


def _run():
    try:
        while True:
            try:
                access_token, expires_in = _authenticate()
                home_id = _fetch_home_id(access_token)
            except Exception:
                _log.exception("yolink: auth failed; retrying in 60s")
                time.sleep(60)
                continue

            try:
                _hydrate_states(access_token)
            except Exception:
                _log.exception("yolink: hydrate failed; continuing without initial state")

            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=str(uuid.uuid4()),
                userdata={"home_id": home_id},
            )
            client.username_pw_set(access_token, "")
            client.on_connect = _on_connect
            client.on_disconnect = _on_disconnect
            client.on_message = _on_message
            try:
                client.connect(_MQTT_HOST, _MQTT_PORT, keepalive=60)
            except Exception:
                _log.exception("yolink: mqtt connect failed; retrying in 60s")
                time.sleep(60)
                continue

            client.loop_start()
            # Re-auth a few minutes before the token expires
            time.sleep(max(60, expires_in - 300))
            client.loop_stop()
            client.disconnect()
    finally:
        _log.error("yolink: thread exiting; signaling SIGTERM to process for restart")
        os.kill(os.getpid(), signal.SIGTERM)


def start():
    if not len(config.Leak):
        _log.info("yolink: no Leak devices in config.md, skipping")
        return

    if not (config.config.secrets.yolink_id and config.config.secrets.yolink_secret):
        _log.info("yolink: secrets not set, skipping")
        return

    global _states
    _states = LockedDict({device.value: SensorState(name=device.name, device_id=device.value) for device in config.Leak})
    threading.Thread(target=_run, name="yolink-mqtt", daemon=True).start()
