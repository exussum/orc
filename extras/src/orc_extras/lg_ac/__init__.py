"""LG window-AC local control (ThinQ2 "clip" protocol).

Replaces LG's cloud: serves the device's enrollment over HTTP, runs the embedded
MQTT broker it connects to, decodes its TLV state, and exposes control. The
enrollment routes live on the ``web`` blueprint (mounted at ``/api/lg_ac/enroll``);
nginx presents the LG cert on :443 and rewrites the device's root paths to it.
"""

from functools import partial
from typing import Any, NamedTuple

from command_cfg import scalar

import orc_extras.lg_ac
from orc.kernel.loader import Cast, load_plugin_config
from orc.model import AcState, AppContext, DeviceStatus, LogSourceEnum, Secrets
from orc_extras.lg_ac import api, web
from orc_extras.lg_ac.dal.broker import amqtt as broker
from orc_extras.lg_ac.dal.capture import memory as capture
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


class LogSource(LogSourceEnum):
    LG_AC = "lg ac"


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
    ctx.plugin_state[orc_extras.lg_ac] = State(s, thinq)
    secrets: Secrets = ctx.config.secrets
    api.configure(secrets[_SECRET_CA_CERT].encode(), secrets[_SECRET_CA_KEY].encode())
    broker.start(s.mqtts_advertise, secrets[_SECRET_SERVER_CERT].encode(), secrets[_SECRET_SERVER_KEY].encode(), s.mqtt_port)
    if s.capture:
        thinq.add_raw_listener(capture.record)  # buffer recent wire frames in memory
    thinq.set_event_listener(lambda msg: ctx.api.log(LogSource.LG_AC, msg, amend=True))
    thinq.start("127.0.0.1", s.mqtt_port, clip_ids=[str(device.value) for device in ctx.config.devices.AC])
    ctx.api.set_ac_handler(partial(_handle_ac, thinq))
    ctx.api.set_ac_state_handler(partial(_ac_state, thinq))
    ctx.api.set_ac_temperature_handler(partial(_ac_temperature, thinq))
    ctx.api.add_state_provider("AC", partial(_ac_status, thinq, ctx))


def _ac_status(transport: Transport, ctx: AppContext) -> list[DeviceStatus]:
    rows = []
    connected = transport.devices()
    for device in ctx.config.devices.AC:
        device_id = str(device.value)
        state = transport.fetch_state(device_id)
        rows.append(
            DeviceStatus(
                name=device.name,
                label=device.label,
                details={
                    "connected": device_id in connected,
                    "power": state.power,
                    "mode": state.mode,
                    "fan": state.fan_mode,
                    "target": state.temperature,
                    "current": state.current_temperature,
                },
            )
        )
    return rows


def _ac_state(transport: Transport, device: Any) -> AcState | None:
    state = transport.fetch_state(str(device.value))  # unknown/stale id yields an empty state
    if state.power is None:
        return None
    elif state.power == "OFF":
        return AcState.OFF
    return AcState.__members__.get((state.mode or "").upper(), AcState.ON)


def _ac_temperature(transport: Transport, device: Any) -> int | None:
    state = transport.fetch_state(str(device.value))
    return None if state.temperature is None or state.power == "OFF" else round(state.temperature)


def _handle_ac(transport: Transport, device: Any, state: str | None, mode: str | None, fan: str | None, temp: int | None) -> None:
    """Drive the AC from orc's /device/ page AC card (mode/fan/temp in °F)."""
    device_id = str(device.value)
    if device_id not in transport.devices():
        return  # unknown/stale clip id: command nothing rather than the wrong AC
    if state == "off":
        transport.publish_command(device_id, {"mode": "off"})
        return
    # a setpoint frame must carry mode, so an omitted mode keeps the device's current one
    values: dict[str, object] = {"mode": mode or transport.fetch_state(device_id).mode or "cool"}
    if fan:
        values["fan_mode"] = fan
    if temp is not None:
        values["temperature"] = round((temp - 32) * 5 / 9, 1)  # UI is °F; the codec wants °C
    transport.publish_command(device_id, values)


def declare(declarations: Any) -> None:
    declarations.declare(
        setup=[setup],
        blueprints={"enroll": web.enroll},
    )
