"""Flask blueprint for the LG AC.

Mounted by orc at ``/api/lg_ac/enroll``. The device hits the enrollment routes at
the domain root (``/route`` etc.); nginx terminates TLS on :443 with the LG
CA-signed cert and rewrites those root paths onto this blueprint.
"""

import socket

from flask import Blueprint, jsonify, request
from flask.wrappers import Response

from orc_extras.lg_ac import api, settings
from orc_extras.lg_ac.dal.mqtt import thinq

enroll = Blueprint("lg_ac", __name__)


@enroll.get("/route")
def route() -> Response:
    s = settings.current()
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
    return jsonify(api.cert_response(signed))


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
