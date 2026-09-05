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
from orc.model import AppContext, Secrets
from orc_extras.lg_ac import api, settings, web
from orc_extras.lg_ac.dal.broker import amqtt as broker
from orc_extras.lg_ac.dal.capture import memory as capture
from orc_extras.lg_ac.dal.mqtt import thinq

CONFIG = "orc_extras/lg_ac"
GRAMMAR = """
setting <key> <value>
"""
_SECRET_CA_CERT = "LG_THINQ_CA_CERT"
_SECRET_CA_KEY = "LG_THINQ_CA_KEY"
_SECRET_SERVER_CERT = "LG_THINQ_SERVER_CERT"
_SECRET_SERVER_KEY = "LG_THINQ_SERVER_KEY"


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
                    "fqdn": Cast.fqdn,
                    "https_advertise": Cast.int,
                    "mqtt_port": Cast.int,
                    "mqtts_advertise": Cast.int,
                    "capture": Cast.bool,
                },
            ),
        },
    )
    s = cfg.setting
    if s.fqdn.endswith(".example"):
        raise RuntimeError("lg_ac: set 'fqdn' in lg_ac.orc to this server's real FQDN (still the .example placeholder)")
    settings.set_current(s)
    secrets: Secrets = ctx.config.secrets
    api.configure(secrets[_SECRET_CA_CERT].encode(), secrets[_SECRET_CA_KEY].encode())
    broker.start(s.mqtts_advertise, secrets[_SECRET_SERVER_CERT].encode(), secrets[_SECRET_SERVER_KEY].encode(), s.mqtt_port)
    if s.capture:
        thinq.add_raw_listener(capture.record)  # buffer recent wire frames in memory
    _wait_for_port("127.0.0.1", s.mqtt_port)
    thinq.start("127.0.0.1", s.mqtt_port)


def declare(declarations: Any) -> None:
    # No state provider: the AC shows on the /device/ page via orc's built-in `AC`
    # device type, not on the system page. This plugin only serves enrollment and
    # the command channel.
    declarations.declare(
        setup=[setup],
        blueprints={"enroll": web.enroll},
    )
