import socket
from dataclasses import asdict
from typing import TYPE_CHECKING, cast

from flask import Blueprint, current_app, jsonify, request
from flask.wrappers import Response

import orc_extras.lg_ac as lg_ac
from orc import model as m
from orc_extras.lg_ac import api
from orc_extras.lg_ac.dal.mqtt.thinq import SOURCE
from orc_extras.lg_ac.model import LogSource

if TYPE_CHECKING:
    from orc.view import OrcFlask

enroll = Blueprint("lg_ac", __name__)
app = cast("OrcFlask", current_app)


@enroll.get("/route")
def route() -> Response:
    s = app.orc.plugin_state[lg_ac].settings
    mqtt_ip = socket.gethostbyname(s.fqdn)  # the device connects to the broker by IP
    return jsonify(api.route(s.hostname, s.https_advertise, mqtt_ip, s.mqtts_advertise))


@enroll.get("/route/certificate")
def route_certificate() -> Response:
    if request.args.get("name"):
        return jsonify(api.cert_response(api.ca().cert_pem))
    return jsonify({"resultCode": "0000", "result": ["common-server", "aws-iot"]})


@enroll.post("/device/<device_id>/certificate")
def device_certificate(device_id: str) -> Response:
    body = request.get_json(force=True)
    signed = api.sign_device_csr(body["csr"].encode(), device_id)
    app.orc.api.log(LogSource.LG_AC, f"AC {device_id[:8]}: paired", m.Broker(id=device_id, source=SOURCE))
    return jsonify(api.cert_response(signed))


@enroll.get("/devices")
def devices() -> Response:
    return jsonify(app.orc.plugin_state[lg_ac].transport.devices())


@enroll.get("/capture")
def capture_dump() -> Response:
    return jsonify(app.orc.plugin_state[lg_ac].capture.dump())


@enroll.get("/state")
def state() -> Response:
    transport = app.orc.plugin_state[lg_ac].transport
    device_id = request.args.get("device") or transport.default_device()
    if device_id is None:
        return jsonify({"error": "no device"})
    return jsonify(asdict(transport.fetch_state(device_id)))


@enroll.post("/command")
def command() -> Response:
    transport = app.orc.plugin_state[lg_ac].transport
    body = dict(request.get_json(force=True))
    device_id = body.pop("device", None) or transport.default_device()
    unit = next((d for d in app.orc.config.devices.AC if str(d.value) == device_id), None)
    if unit is None:
        return jsonify({"error": "no device"})
    current = transport.fetch_state(device_id)
    if body.get("mode") == "off":
        asked = m.AcState(power=m.OFF)
    else:
        asked = m.AcState(
            m.ON,
            body.get("mode") or current.mode or "cool",
            body.get("fan_mode") or current.fan_mode,
            body.get("temperature") or current.temperature,
        )
        if asked.fan_mode is None or asked.temperature is None:
            return jsonify({"error": "a setpoint needs fan_mode and temperature"})
    entry = app.orc.api.log(LogSource.LG_AC, f"AC {device_id[:8]}: {asked}", m.Manual("lg_ac"))
    app.orc.api.device_command(unit.name, str(asked), entry)
    return jsonify({"status": "sent", "device": device_id, "command": str(asked)})
