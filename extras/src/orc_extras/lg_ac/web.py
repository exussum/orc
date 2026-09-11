import socket
from typing import TYPE_CHECKING, cast

from flask import Blueprint, current_app, jsonify, request
from flask.wrappers import Response

import orc_extras.lg_ac as lg_ac
from orc_extras.lg_ac import api
from orc_extras.lg_ac.dal.capture import memory as capture
from orc_extras.lg_ac.dal.mqtt import thinq
from orc_extras.lg_ac.model import Settings

if TYPE_CHECKING:
    from orc.view import OrcFlask

enroll = Blueprint("lg_ac", __name__)
app = cast("OrcFlask", current_app)


@enroll.get("/route")
def route() -> Response:
    s: Settings = app.orc.plugin_state[lg_ac]
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
    thinq.event(f"AC {device_id[:8]} paired")
    return jsonify(api.cert_response(signed))


@enroll.get("/devices")
def devices() -> Response:
    return jsonify(thinq.devices())


@enroll.get("/capture")
def capture_dump() -> Response:
    return jsonify(capture.dump())


@enroll.get("/state")
def state() -> Response:
    device_id = request.args.get("device") or thinq.default_device()
    if device_id is None:
        return jsonify({"error": "no device"})
    return jsonify(thinq.fetch_state(device_id)._asdict())


@enroll.post("/command")
def command() -> Response:
    body = dict(request.get_json(force=True))
    device_id = body.pop("device", None) or thinq.default_device()
    if device_id is None:
        return jsonify({"error": "no device"})
    thinq.publish_command(device_id, body)
    return jsonify({"status": "sent", "device": device_id, "command": body})
