"""LG window-AC local control (ThinQ2 "clip" protocol).

Replaces LG's cloud: serves the device's enrollment over HTTP, runs the embedded
MQTT broker it connects to, decodes its TLV state, and exposes control. The
enrollment routes live on the ``web`` blueprint (mounted at ``/api/lg_ac/enroll``);
nginx presents the LG cert on :443 and rewrites the device's root paths to it.
"""

import socket
import time
from typing import Any

from command_cfg import scalar

from orc.loader import Cast, load_plugin_config
from orc.model import AppContext, DeviceStatus
from orc_extras.lg_ac import api, settings, web
from orc_extras.lg_ac.dal.broker import amqtt as broker
from orc_extras.lg_ac.dal.capture import file as capture
from orc_extras.lg_ac.dal.mqtt import thinq

CONFIG = "orc_extras/lg_ac"
GRAMMAR = """
setting <key> <value>
"""
_CAPTURE_PATH = "capture.jsonl"


def _wait_for_port(host: str, port: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def setup(ctx: AppContext) -> None:
    cfg = load_plugin_config(
        CONFIG,
        ctx.config,
        GRAMMAR,
        serializers={
            "setting": scalar(
                settings.Settings,
                types={
                    "hostname": Cast.fqdn,
                    "https_advertise": Cast.int,
                    "mqtt_host": Cast.ip,
                    "mqtt_port": Cast.int,
                    "mqtts_advertise": Cast.int,
                    "capture": Cast.bool,
                },
            ),
        },
    )
    s = cfg.setting
    settings.set_current(s)
    api.configure(s.ca_cert, s.ca_key)
    broker.start(s.mqtts_advertise, s.ca_cert, s.server_cert, s.server_key, s.mqtt_port)
    if s.capture:
        capture.configure(_CAPTURE_PATH)
        thinq.add_raw_listener(capture.record)
    _wait_for_port("127.0.0.1", s.mqtt_port)
    thinq.start("127.0.0.1", s.mqtt_port)


def ac_state() -> list[DeviceStatus]:
    device_id = thinq.default_device()
    if device_id is None:
        return []
    state = thinq.fetch_state(device_id)
    return [DeviceStatus(name="Air Conditioner", label="Air Conditioner", details=dict(state._asdict()))]


def declare(declarations: Any) -> None:
    declarations.declare(
        setup=[setup],
        state_providers={"Air Conditioner": ac_state},
        blueprints={"enroll": web.enroll},
    )
