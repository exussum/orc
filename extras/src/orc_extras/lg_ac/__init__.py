"""LG window-AC local control (ThinQ2 "clip" protocol).

Replaces LG's cloud: serves the device's enrollment over HTTP, runs the embedded
MQTT broker it connects to, decodes its TLV state, and exposes control. The
enrollment routes live on the ``web`` blueprint (mounted at ``/api/lg_ac/enroll``);
nginx presents the LG cert on :443 and rewrites the device's root paths to it.
"""

from functools import partial
from typing import Any, NamedTuple

from command_cfg import scalar
from orc_engine import cast

import orc_extras.lg_ac
from orc.kernel.loader import load_plugin_config
from orc.model import AppContext, Secrets
from orc_extras.lg_ac import api, plugins, web
from orc_extras.lg_ac.dal.broker import amqtt as broker
from orc_extras.lg_ac.dal.capture import Capture
from orc_extras.lg_ac.dal.mqtt import thinq
from orc_extras.lg_ac.dal.mqtt.interfaces import Transport
from orc_extras.lg_ac.model import Settings

CONFIG = "orc_extras/lg_ac"
GRAMMAR = """
setting <key> <value>
"""


class State(NamedTuple):
    settings: Settings
    transport: Transport
    capture: Capture


_SECRET_CA_CERT = "LG_THINQ_CA_CERT"
_SECRET_CA_KEY = "LG_THINQ_CA_KEY"
_SECRET_SERVER_CERT = "LG_THINQ_SERVER_CERT"
_SECRET_SERVER_KEY = "LG_THINQ_SERVER_KEY"


def setup(ctx: AppContext) -> None:
    cfg = load_plugin_config(
        CONFIG,
        ctx.config,
        GRAMMAR,
        serializers={
            "setting": scalar(
                Settings,
                types={
                    "hostname": cast.fqdn,
                    "fqdn": cast.fqdn,
                    "https_advertise": cast.int,
                    "mqtt_port": cast.int,
                    "mqtts_advertise": cast.int,
                    "capture": cast.bool,
                },
            ),
        },
    )
    s = cfg.setting
    if s.fqdn.endswith(".example"):
        raise RuntimeError("lg_ac: set 'fqdn' in lg_ac.orc to this server's real FQDN (still the .example placeholder)")
    capture = Capture()
    ctx.plugin_state[orc_extras.lg_ac] = State(s, thinq, capture)
    secrets: Secrets = ctx.config.secrets
    api.configure(secrets.other[_SECRET_CA_CERT].encode(), secrets.other[_SECRET_CA_KEY].encode())
    broker.start(s.mqtts_advertise, secrets.other[_SECRET_SERVER_CERT].encode(), secrets.other[_SECRET_SERVER_KEY].encode(), s.mqtt_port)
    if s.capture:
        thinq.add_raw_listener(capture.record)  # buffer recent wire frames in memory
    thinq.set_event_listener(partial(plugins._on_event, ctx))
    thinq.start("127.0.0.1", s.mqtt_port, clip_ids=[str(device.value) for device in ctx.config.devices.AC])
    ctx.api.set_ac(plugins.Ac(thinq))
    ctx.api.add_state_provider("AC", partial(plugins._ac_status, thinq, ctx))


def declare(declarations: Any) -> None:
    declarations.declare(
        setup=[setup],
        blueprints={"enroll": web.enroll},
        secrets={
            _SECRET_CA_CERT: cast.pem_cert,
            _SECRET_CA_KEY: cast.pem_key,
            _SECRET_SERVER_CERT: cast.pem_cert,
            _SECRET_SERVER_KEY: cast.pem_key,
        },
    )
