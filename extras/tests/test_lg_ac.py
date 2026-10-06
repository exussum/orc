from dataclasses import asdict
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from flask import Flask
from orc_extras import lg_ac
from orc_extras.lg_ac import api, plugins, web
from orc_extras.lg_ac import model as m
from orc_extras.lg_ac.dal.capture import Capture
from orc_extras.lg_ac.dal.mqtt import stub
from orc_extras.lg_ac.dal.mqtt.thinq import Thinq

from orc.model import OFF, ON, AcMode, AcState, Broker, Device, DeviceStatus, Source, Status

MODEL = "WIN_056905_WW"
DEVICE_ID = "clip-123"
FM = api.load_fieldmap(MODEL)
assert FM is not None


@pytest.fixture(scope="module")
def ca_pem():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-ca")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime(2026, 1, 1, tzinfo=UTC))
        .not_valid_after(datetime(2037, 1, 1, tzinfo=UTC))
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption())
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    api.configure(cert_pem, key_pem)
    return cert_pem


@pytest.fixture(scope="module")
def device_csr():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "factory-name")])
    return x509.CertificateSigningRequestBuilder().subject_name(subject).sign(key, hashes.SHA256())


# --- TLV codec ---


@pytest.mark.parametrize(
    ("value", "length"),
    [(0x0, 0), (0xF, 0), (0x10, 1), (0xFF, 1), (0x100, 2), (0xFFFF, 2), (0x10000, 3)],
)
def test_tlv_field_of_picks_the_smallest_length(value, length):
    assert m.TLVField.of(0x1F7, value).length == length


def test_dissect_round_trips_encoded_fields():
    fields = [m.TLVField.of(0x1F7, 1), m.TLVField.of(0x1FE, 44), m.TLVField.of(0x1F5, 0x123)]
    packet = api.dissect(m.DissectedPacket(fields=fields).rebuild())
    assert packet.fields == fields
    assert packet.remainder == b""


def test_dissect_keeps_undecodable_tail_as_remainder():
    whole = m.TLVField.of(0x1F7, 1).encode()
    truncated = m.TLVField.of(0x1FE, 0x1234).encode()[:-1]
    packet = api.dissect(whole + truncated)
    assert packet.fields == [m.TLVField.of(0x1F7, 1)]
    assert packet.remainder == truncated


def test_crc16_is_xmodem():
    assert api._crc16(b"123456789") == 0x31C3


def test_build_query_frames_the_tlv_with_a_crc():
    frame = api.build_query(api.Query.VALUES)
    body = frame[2:-2]
    tlv = m.TLVField.of(0x1F5, api.Query.VALUES).encode()
    assert frame[:2] == b"\x01\x01"
    assert body == bytes([0x04, 0x00, 0x00, 0x00, 0x65, 2, 2, 1, len(tlv)]) + tlv
    assert frame[-2:] == bytes([api._crc16(body) >> 8, api._crc16(body) & 0xFF])


def _device_frame(tlv: bytes, marker: int = 0x87) -> bytes:
    return b"\x00\x00" + bytes([0x04, 0x00, 0x00, 0x00, marker, 0x02, 0x01, 0, len(tlv)]) + tlv + b"\x00\x00"


def test_frame_tlv_parses_a_device_frame():
    tlv = m.TLVField.of(0x1F7, 1).encode() + m.TLVField.of(0x1FE, 44).encode()
    packet = api.frame_tlv(_device_frame(tlv))
    assert packet is not None
    assert packet.fields == [m.TLVField.of(0x1F7, 1), m.TLVField.of(0x1FE, 44)]


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("too short", b"\x00" * 12),
        ("wrong marker", _device_frame(m.TLVField.of(0x1F7, 1).encode(), marker=0x11)),
        ("wrong length byte", _device_frame(m.TLVField.of(0x1F7, 1).encode()) + b"\x00"),
    ],
)
def test_frame_tlv_rejects_malformed_frames(name, payload):
    assert api.frame_tlv(payload) is None


# --- Field map and state decoding ---


def test_load_fieldmap_handles_unknown_models():
    assert api.load_fieldmap("WIN_000000_XX") is None
    assert api.load_fieldmap(MODEL) is FM


@pytest.mark.parametrize(
    ("raw", "state"),
    [
        (
            {FM.power: 1, FM.mode: 0, FM.fan: 2, FM.current_temp: 50, FM.target_temp: 44},
            AcState("on", "cool", "low", 72, current_temperature=77),
        ),
        ({FM.power: 0, FM.mode: 0}, AcState("off", "cool")),
        ({FM.power: 1, FM.mode: 99, FM.fan: 99}, AcState("on", None, None)),
        ({}, AcState()),
    ],
)
def test_state_from_raw(raw, state):
    assert api.state_from_raw(FM, raw) == state


# --- Command encoding ---


@pytest.mark.parametrize(
    ("values", "fields"),
    [
        ({"power": "on"}, [("power", 1)]),
        ({"power": "off"}, [("power", 0)]),
        ({"mode": "off"}, [("power", 0)]),
        ({"mode": "cool"}, [("power", 1), ("mode", 0)]),
        ({"fan_mode": "high"}, [("fan", 6)]),
        ({"temperature": 22}, [("target_temp", 44)]),
        ({"temperature": 35}, [("target_temp", 60)]),
        ({"temperature": 10}, [("target_temp", 32)]),
    ],
)
def test_encode_command_maps_values_to_tlv_fields(values, fields):
    packet = api.dissect(api.encode_command(FM, values))
    assert [(f.type_id, f.value) for f in packet.fields] == [(getattr(FM, name), value) for name, value in fields]


def test_encode_command_rejects_unknown_fields():
    with pytest.raises(KeyError):
        api.encode_command(FM, {"swing": 1})


def test_build_command_round_trips_through_the_codec():
    frame = api.build_command(FM, {"mode": "cool", "fan_mode": "low", "temperature": 22})
    body = frame[2:-2]
    assert frame[:2] == b"\x01\x01"
    assert body[:9] == bytes([0x04, 0x00, 0x00, 0x00, 0x65, 2, 1, 1, len(body) - 9])
    raw = {f.type_id: f.value for f in api.dissect(body[9:]).fields}
    assert api.state_from_raw(FM, raw) == AcState("on", "cool", "low", temperature=72)


# --- Provisioning responses ---


def test_route_advertises_api_and_mqtt_servers():
    assert api.route("common.lgthinq.com", 443, "10.0.0.5", 8883) == {
        "resultCode": "0000",
        "result": {"apiServer": "https://common.lgthinq.com:443", "mqttServer": "ssl://10.0.0.5:8883"},
    }


def test_deploy_echoes_the_requested_phase():
    payload = api.deploy(DEVICE_ID, 7, "preDeploy")
    assert payload["did"] == DEVICE_ID
    assert payload["mid"] == 7
    assert payload["data"]["provisioningType"] == "preDeploy"
    assert payload["data"]["appInfo"]["publication"]["message"] == f"clip/message/devices/{DEVICE_ID}"


# --- Certificates ---


def test_ca_requires_configure(monkeypatch):
    monkeypatch.setattr(api, "_ca", None)
    with pytest.raises(RuntimeError):
        api.ca()


def test_configure_rejects_non_rsa_keys(ca_pem):
    key = ec.generate_private_key(ec.SECP256R1())
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption())
    with pytest.raises(TypeError):
        api.configure(ca_pem, key_pem)


@pytest.mark.parametrize("encoding", [serialization.Encoding.PEM, serialization.Encoding.DER])
def test_sign_device_csr_issues_a_client_cert(ca_pem, device_csr, encoding):
    signed = api.sign_device_csr(device_csr.public_bytes(encoding), DEVICE_ID)
    cert = x509.load_pem_x509_certificate(signed)
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == DEVICE_ID
    assert cert.issuer == x509.load_pem_x509_certificate(ca_pem).subject
    assert cert.public_key() == device_csr.public_key()
    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert ExtendedKeyUsageOID.CLIENT_AUTH in eku


# --- Enrollment routes ---


@pytest.fixture(autouse=True)
def reset_stub():
    stub.reset()


@pytest.fixture
def client():
    app = Flask(__name__)
    settings = m.Settings(hostname="common.lgthinq.com", fqdn="orc.local", https_advertise=443, mqtts_advertise=8883)
    units = (SimpleNamespace(value=DEVICE_ID, name="LIVING", label="Living AC"),)
    app.orc = SimpleNamespace(  # type: ignore[attr-defined]
        plugin_state={lg_ac: lg_ac.State(settings, stub, Capture())},
        api=MagicMock(),
        config=SimpleNamespace(devices=SimpleNamespace(AC=units)),
    )
    app.register_blueprint(web.enroll)
    return app.test_client()


def test_route_endpoint_resolves_the_broker_ip(client, monkeypatch):
    monkeypatch.setattr(web.socket, "gethostbyname", lambda fqdn: {"orc.local": "10.0.0.5"}[fqdn])
    assert client.get("/route").get_json() == api.route("common.lgthinq.com", 443, "10.0.0.5", 8883)


def test_route_certificate_serves_the_ca(client, ca_pem):
    body = client.get("/route/certificate?name=common-server").get_json()
    assert body["result"]["certificatePem"] == ca_pem.decode()


def test_route_certificate_lists_server_names_without_one(client):
    assert client.get("/route/certificate").get_json() == {"resultCode": "0000", "result": ["common-server", "aws-iot"]}


def test_device_certificate_signs_the_posted_csr(client, ca_pem, device_csr):
    csr_pem = device_csr.public_bytes(serialization.Encoding.PEM).decode()
    body = client.post(f"/device/{DEVICE_ID}/certificate", json={"csr": csr_pem}).get_json()
    cert = x509.load_pem_x509_certificate(body["result"]["certificatePem"].encode())
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == DEVICE_ID


def test_state_endpoint_reports_the_default_device(client):
    stub.reset(states={DEVICE_ID: AcState("on", "cool", "low", 22.0, current_temperature=25.0)}, default=DEVICE_ID)
    assert client.get("/state").get_json() == asdict(AcState("on", "cool", "low", 22.0, current_temperature=25.0))


def test_state_endpoint_errors_with_no_device(client):
    assert client.get("/state").get_json() == {"error": "no device"}


@pytest.mark.parametrize(
    ("body", "command"),
    [({"device": DEVICE_ID, "mode": "cool", "temperature": 72}, "cool:low:72"), ({"mode": "off"}, "off")],
)
def test_command_endpoint_runs_the_device_command(client, body, command):
    stub.reset(states={DEVICE_ID: AcState("on", "dry", "low", 22.0, current_temperature=25.0)}, devices=[DEVICE_ID], default=DEVICE_ID)
    assert client.post("/command", json=body).get_json() == {"status": "sent", "device": DEVICE_ID, "command": command}
    client.application.orc.api.device_command.assert_called_once_with("LIVING", command, ANY)


@pytest.mark.parametrize(("default", "body"), [(None, {"mode": "cool"}), (DEVICE_ID, {"mode": "cool"})])
def test_command_endpoint_refuses_a_half_request(client, default, body):
    stub.reset(default=default)
    assert "error" in client.post("/command", json=body).get_json()


@pytest.mark.parametrize(
    "state", [AcState("off", "cool", "low", 77, current_temperature=70), AcState("on", "dry", "low", 77, current_temperature=70)]
)
def test_change_logs_the_state_as_the_command_it_answers(ctx, state):
    plugins._on_change(
        ctx,
        Status(
            Device(DEVICE_ID, "Living AC", "lg_ac"), "state", AcState("on", "cool", "low", 70, current_temperature=70), state, Source.ORC
        ),
    )
    ctx.api.log.assert_called_once_with(m.LogSource.LG_AC, ANY, Broker(id=DEVICE_ID, source="lg_ac", value=state))


def test_change_line_names_what_moved(ctx):
    before, after = AcState("on", "cool", "low", 77, current_temperature=70), AcState("on", "dry", "low", 75, current_temperature=71)
    plugins._on_change(ctx, Status(Device(DEVICE_ID, "Living AC", "lg_ac"), "state", before, after, Source.ORC))
    assert ctx.api.log.call_args[0][1] == "AC clip-123: mode cool → dry, temperature 77 → 75"
    plugins._on_change(ctx, Status(Device(DEVICE_ID, "Living AC", "lg_ac"), "state", AcState(), AcState("off"), Source.ORC))
    assert ctx.api.log.call_count == 1


def test_change_ignores_other_sources_and_attributes(ctx):
    plugins._on_change(ctx, Status(Device(1, "lamp", "hubitat"), "state", None, None, Source.ORC))
    plugins._on_change(ctx, Status(Device(DEVICE_ID, "Living AC", "lg_ac"), "power", "off", "on", Source.ORC))
    ctx.api.log.assert_not_called()


def test_an_external_change_is_left_to_the_external_plugin(ctx):
    plugins._on_change(
        ctx,
        Status(
            Device(DEVICE_ID, "Living AC", "lg_ac"), "state", AcState("on", "cool", "low"), AcState("on", "dry", "low"), Source.EXTERNAL
        ),
    )
    ctx.api.log.assert_not_called()


def test_state_prints_as_a_command():
    assert str(AcState("on", "cool", "low", 77, current_temperature=70)) == "cool:low:77"
    assert str(AcState(power="off", mode="cool")) == "off"
    assert str(AcState(power="on")) == "on"


# --- The adapter ---


def _frame(values):
    tlv = api.encode_command(FM, values)
    return (bytes([1, 1, 4, 0, 0, 0, 0x87, 2, 1, 0, len(tlv)]) + tlv + b"\x00\x00").hex()


def _data(message):
    return bytes.fromhex(message.payload["data"])


@pytest.fixture
def adapter():
    adapter = Thinq({"clip-1": "Living AC", "clip-2": "Bedroom AC"})
    adapter.sent = []
    adapter.attach(adapter.sent.append)
    return adapter


def _provision(adapter, device_id="clip-1"):
    return adapter.decode(f"clip/provisioning/devices/{device_id}", {"cmd": "preDeploy", "kind": MODEL})


def _report(adapter, **values):
    return adapter.decode("clip/message/devices/clip-1", {"cmd": "device_packet", "data": _frame(values)})


def test_provisioning_learns_the_model_and_answers(adapter):
    assert _provision(adapter) == ()
    (reply,) = adapter.sent
    assert (reply.topic, reply.payload["cmd"], reply.payload["data"]["provisioningType"]) == (
        "lime/devices/clip-1",
        "completeProvisioning",
        "preDeploy",
    )
    assert adapter.devices() == ["clip-1"]
    assert adapter.decode("clip/provisioning/devices/clip-1", {"cmd": "completeProvisioning"}) == ()
    assert len(adapter.sent) == 1


def test_provisioning_ack_polls_the_unit(adapter):
    assert adapter.decode("clip/message/devices/clip-1", {"cmd": "completeProvisioning_ack"}) == ()
    assert [_data(r) for r in adapter.sent] == [api.build_query(api.Query.CAPABILITIES), api.build_query(api.Query.VALUES)]


def test_first_packet_updates_state_without_a_change(adapter):
    _provision(adapter)
    decoded = adapter.decode(
        "clip/message/devices/clip-1", {"cmd": "device_packet", "data": _frame({"mode": "cool", "fan_mode": "low", "temperature": 22})}
    )
    assert decoded == ()
    assert adapter.fetch_state("clip-1") == AcState("on", "cool", "low", temperature=72)
    (state,) = adapter.snapshot()
    assert state.device == Device("clip-1", "Living AC", "lg_ac")
    assert state.attributes["mode"] == "cool"


def test_later_packet_merges_and_reports_one_state_change(adapter):
    _provision(adapter)
    adapter.decode(
        "clip/message/devices/clip-1", {"cmd": "device_packet", "data": _frame({"mode": "cool", "fan_mode": "low", "temperature": 22})}
    )
    decoded = adapter.decode("clip/message/devices/clip-1", {"cmd": "device_packet", "data": _frame({"mode": "dry"})})
    assert decoded == (
        Status(
            Device("clip-1", "Living AC", "lg_ac"),
            "state",
            AcState("on", "cool", "low", temperature=72),
            AcState("on", "dry", "low", temperature=72),
            Source.EXTERNAL,
        ),
    )


@pytest.mark.parametrize(
    ("provisioned", "earlier", "frame"),
    [
        (False, [], {"mode": "cool"}),
        (True, [{"mode": "cool", "fan_mode": "low", "temperature": 22}, {"mode": "dry"}], {"mode": "dry"}),
    ],
)
def test_packets_that_change_nothing_report_nothing(adapter, provisioned, earlier, frame):
    if provisioned:
        _provision(adapter)
    for values in earlier:
        _report(adapter, **values)
    assert _report(adapter, **frame) == ()


def test_timesync_is_answered(adapter):
    assert adapter.decode("clip/message/devices/clip-1", {"cmd": "req_timesync"}) == ()
    (reply,) = adapter.sent
    assert (reply.topic, reply.payload["cmd"]) == ("lime/devices/clip-1", "resp_timesync")


def test_start_nudges_each_configured_unit(adapter):
    adapter.start()
    assert [(msg.topic, msg.retain, _data(msg)) for msg in adapter.sent] == [
        ("lime/devices/clip-1", True, api.build_query(api.Query.VALUES)),
        ("lime/devices/clip-2", True, api.build_query(api.Query.VALUES)),
    ]


def test_nothing_is_sent_before_a_publisher_is_attached():
    adapter = Thinq({"clip-1": "Living AC"})
    adapter.start()
    assert adapter.decode("clip/message/devices/clip-1", {"cmd": "req_timesync"}) == ()


def test_tap_sees_every_message():
    seen = []
    adapter = Thinq({}, lambda topic, doc: seen.append((topic, doc)))
    adapter.decode("clip/message/devices/x", {"cmd": "req_timesync"})
    assert seen == [("clip/message/devices/x", {"cmd": "req_timesync"})]


@pytest.mark.parametrize(
    ("held", "command", "sent"),
    [
        (None, AcState(power=OFF), {"mode": "off"}),
        (None, AcState(ON, AcMode.COOL, "low", temperature=72), {"mode": "cool", "fan_mode": "low", "temperature": 22.2}),
        (
            {"mode": "cool", "fan_mode": "low", "temperature": 25, "power": "on"},
            AcState(ON, AcMode.COOL, "low", temperature=75),
            {"mode": "cool", "fan_mode": "low", "temperature": 23.9},
        ),
        ({"power": "off"}, AcState(power=OFF), None),
        ({"mode": "dry", "power": "on"}, AcState(ON, "dry"), None),
        ({"mode": "cool", "fan_mode": "low", "temperature": 25, "power": "on"}, AcState(ON, AcMode.COOL, "low", temperature=77), None),
    ],
)
def test_encode_sends_only_what_the_unit_does_not_hold(adapter, held, command, sent):
    _provision(adapter)
    if held:
        _report(adapter, **held)
    messages = adapter.encode(SimpleNamespace(value="clip-1", name="LIVING"), command)
    if sent is None:
        assert messages == ()
    else:
        (msg,) = messages
        assert (msg.topic, _data(msg)) == ("lime/devices/clip-1", api.build_command(FM, sent))


def _asked_for_high_fan(adapter):
    _provision(adapter)
    _report(adapter, mode="cool", fan_mode="low", temperature=22, power="on")
    adapter.encode(SimpleNamespace(value="clip-1", name="LIVING"), AcState(ON, AcMode.COOL, "high", temperature=72))


@pytest.mark.parametrize(("report", "source"), [({"fan_mode": "high"}, Source.ORC), ({"mode": "dry"}, Source.EXTERNAL)])
def test_a_report_is_orc_only_when_it_shows_what_was_asked(adapter, report, source):
    _asked_for_high_fan(adapter)
    (status,) = _report(adapter, **report)
    assert status.source is source


def test_an_answered_command_is_forgotten(adapter):
    _asked_for_high_fan(adapter)
    _report(adapter, fan_mode="high")
    (status,) = _report(adapter, fan_mode="mid")
    assert status.source is Source.EXTERNAL


def test_encode_stale_id_commands_nothing(adapter):
    _provision(adapter)
    assert adapter.encode(SimpleNamespace(value="clip-stale", name="X"), AcState(power=OFF)) == ()


def test_encode_without_a_field_map_fails_loudly(adapter):
    adapter.decode("clip/provisioning/devices/clip-1", {"cmd": "preDeploy", "kind": "UNKNOWN"})
    with pytest.raises(RuntimeError):
        adapter.encode(SimpleNamespace(value="clip-1", name="LIVING"), AcState(power=OFF))


def test_ac_status_rows_decode_per_device():
    stub.reset(states={"clip-1": AcState("on", "cool", "low", 72, current_temperature=77)}, devices=["clip-1"])
    ctx = SimpleNamespace(
        config=SimpleNamespace(
            devices=SimpleNamespace(AC=(SimpleNamespace(value="clip-1", name="LIVING_ROOM_AC", label="Living Room AC"),))
        )
    )
    assert plugins._ac_status(stub, ctx) == [
        DeviceStatus(
            name="LIVING_ROOM_AC",
            label="Living Room AC",
            details={"connected": True, "power": "on", "mode": "cool", "fan": "low", "target": 72, "current": 77},
        )
    ]


def test_ac_status_disconnected_device_is_blank():
    ctx = SimpleNamespace(
        config=SimpleNamespace(devices=SimpleNamespace(AC=(SimpleNamespace(value="clip-1", name="LIVING_ROOM_AC", label=None),)))
    )
    assert plugins._ac_status(stub, ctx) == [
        DeviceStatus(
            name="LIVING_ROOM_AC",
            label=None,
            details={"connected": False, "power": None, "mode": None, "fan": None, "target": None, "current": None},
        )
    ]


def test_capture_endpoint_dumps_recorded_frames(client):
    client.application.orc.plugin_state[lg_ac].capture.record("clip/topic", {"cmd": "device_packet", "data": "0102"})
    frames = client.get("/capture").get_json()
    assert frames[-1]["topic"] == "clip/topic"
    assert frames[-1]["payload"] == {"cmd": "device_packet", "data": "0102"}
