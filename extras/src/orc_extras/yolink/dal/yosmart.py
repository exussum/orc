import json
import logging
import uuid
from collections.abc import Sequence
from typing import Any

import paho.mqtt.client as mqtt
import requests

from orc import model as m
from orc_extras.yolink.dal.interfaces import ConnectionCallback, ReportCallback

_AUTH_URL = "https://api.yosmart.com/open/yolink/token"
_API_URL = "https://api.yosmart.com/open/yolink/v2/api"
_MQTT_HOST = "api.yosmart.com"
_MQTT_PORT = 8003

_log = logging.getLogger(__name__)


def authenticate(secrets: m.Secrets, timeout: int) -> tuple[str, int]:
    response = requests.post(
        _AUTH_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": secrets["YOLINK_ID"],
            "client_secret": secrets["YOLINK_SECRET"],
        },
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()
    return body["access_token"], int(body.get("expires_in", 7200))


def fetch_leak_states(access_token: str, device_ids: Sequence[str], timeout: int) -> dict[str, Any]:
    tokens = {d["deviceId"]: d["token"] for d in _api_post(access_token, {"method": "Home.getDeviceList"}, timeout)["devices"]}
    states: dict[str, Any] = {}
    for device_id in device_ids:
        device_token = tokens.get(device_id)
        if device_token is None:
            continue
        try:
            states[device_id] = _api_post(
                access_token, {"method": "LeakSensor.getState", "targetDevice": device_id, "token": device_token}, timeout
            )
        except Exception:
            _log.exception("yolink: getState failed for %s", device_id)
    return states


def connect(access_token: str, on_connection: ConnectionCallback, on_report: ReportCallback, timeout: int) -> "_PahoSession":
    home_id = _api_post(access_token, {"method": "Home.getGeneralInfo"}, timeout)["id"]

    def _on_connect(client: mqtt.Client, userdata: Any, flags: Any, rc: Any, *args: Any) -> None:
        if rc != 0:
            _log.warning("yolink: mqtt connect rc=%s", rc)
            return
        client.subscribe(f"yl-home/{home_id}/+/report", qos=0)
        on_connection(True)

    def _on_disconnect(client: mqtt.Client, userdata: Any, *args: Any) -> None:
        on_connection(False)

    def _on_message(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        parts = msg.topic.split("/")
        if len(parts) < 4 or parts[3] != "report":
            return
        try:
            payload = json.loads(msg.payload.decode())
        except Exception:
            _log.exception("yolink: bad payload on %s", msg.topic)
            return
        on_report(parts[2], payload.get("data") or {})

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=str(uuid.uuid4()))
    client.username_pw_set(access_token, "")
    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect
    client.on_message = _on_message
    client.connect(_MQTT_HOST, _MQTT_PORT, keepalive=60)
    client.loop_start()
    return _PahoSession(client)


class _PahoSession:
    def __init__(self, client: mqtt.Client) -> None:
        self._client = client

    def close(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()


def _api_post(access_token: str, body: dict[str, Any], timeout: int) -> Any:
    response = requests.post(
        _API_URL,
        json=body,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()["data"]
