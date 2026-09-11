from datetime import UTC, datetime

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from flask import Flask
from orc_extras.lg_ac import api, settings, web
from orc_extras.lg_ac import model as m
from orc_extras.lg_ac.dal.capture import memory as capture
from orc_extras.lg_ac.dal.mqtt import thinq

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


def test_state_from_raw_decodes_every_field():
    raw = {FM.power: 1, FM.mode: 0, FM.fan: 2, FM.current_temp: 50, FM.target_temp: 44}
    assert api.state_from_raw(FM, raw) == m.ACState("ON", "cool", "low", 25.0, 22.0)


def test_state_from_raw_reports_mode_off_when_powered_down():
    assert api.state_from_raw(FM, {FM.power: 0, FM.mode: 0}) == m.ACState("OFF", "off")


def test_state_from_raw_leaves_unknown_codes_as_none():
    assert api.state_from_raw(FM, {FM.power: 1, FM.mode: 99, FM.fan: 99}) == m.ACState("ON", None, None)
    assert api.state_from_raw(FM, {}) == m.ACState()


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
    assert api.state_from_raw(FM, raw) == m.ACState("ON", "cool", "low", temperature=22.0)


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


@pytest.fixture
def client():
    settings.set_current(
        settings.Settings(hostname="common.lgthinq.com", fqdn="orc.local", https_advertise=443, mqtt_port=1883, mqtts_advertise=8883)
    )
    app = Flask(__name__)
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


def test_devices_endpoint_lists_connected_devices(client, monkeypatch):
    monkeypatch.setattr(thinq, "devices", lambda: [DEVICE_ID])
    assert client.get("/devices").get_json() == [DEVICE_ID]


def test_state_endpoint_reports_the_default_device(client, monkeypatch):
    monkeypatch.setattr(thinq, "default_device", lambda: DEVICE_ID)
    monkeypatch.setattr(thinq, "fetch_state", lambda device_id: m.ACState("ON", "cool", "low", 25.0, 22.0))
    assert client.get("/state").get_json() == m.ACState("ON", "cool", "low", 25.0, 22.0)._asdict()


def test_state_endpoint_errors_with_no_device(client, monkeypatch):
    monkeypatch.setattr(thinq, "default_device", lambda: None)
    assert client.get("/state").get_json() == {"error": "no device"}


def test_command_endpoint_publishes_to_the_device(client, monkeypatch):
    published = []
    monkeypatch.setattr(thinq, "publish_command", lambda device_id, values: published.append((device_id, values)))
    body = client.post("/command", json={"device": DEVICE_ID, "mode": "cool", "temperature": 22}).get_json()
    assert published == [(DEVICE_ID, {"mode": "cool", "temperature": 22})]
    assert body == {"status": "sent", "device": DEVICE_ID, "command": {"mode": "cool", "temperature": 22}}


def test_command_endpoint_errors_with_no_device(client, monkeypatch):
    monkeypatch.setattr(thinq, "default_device", lambda: None)
    assert client.post("/command", json={"mode": "cool"}).get_json() == {"error": "no device"}


def test_capture_endpoint_dumps_recorded_frames(client):
    capture.record("clip/topic", b"\x01\x02")
    frames = client.get("/capture").get_json()
    assert frames[-1]["topic"] == "clip/topic"
    assert frames[-1]["payload"] == "0102"
