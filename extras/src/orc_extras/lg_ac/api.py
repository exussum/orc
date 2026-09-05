from __future__ import annotations

import base64
import datetime
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.types import CertificatePublicKeyTypes
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from orc_extras.lg_ac import model as m

# --- ThinQ2 TLV codec ---
#
# The field map (TLV ids, mode/fan codes, temp scaling) lives in JSON, NOT here —
# one file per device model at fieldmap/<kind>.json (kind comes from the device's
# preDeploy payload, e.g. WIN_056905_WW). select_model() loads the map for the
# connected device; an unknown model leaves the codec inert (capture-only).
# Override the directory with LG_AC_FIELDMAP_DIR.
_FIELDMAP_DIR = Path(os.environ.get("LG_AC_FIELDMAP_DIR") or Path(__file__).parent / "fieldmap")
_active_model: str | None = None
_fieldmap: dict[str, Any] = {}

POWER = MODE = FAN = CURRENT_TEMP = TARGET_TEMP = 0
MODE_TO_CODE: dict[str, int] = {}
FAN_TO_CODE: dict[str, int] = {}
_MIN_C = 16.0
_MAX_C = 30.0
_TEMP_DIV = 2
_ON = (True, "ON", "on", 1)


def _apply_fieldmap() -> None:
    global POWER, MODE, FAN, CURRENT_TEMP, TARGET_TEMP
    global MODE_TO_CODE, FAN_TO_CODE, _MIN_C, _MAX_C, _TEMP_DIV
    POWER = int(_fieldmap["fields"]["power"], 16)
    MODE = int(_fieldmap["fields"]["mode"], 16)
    FAN = int(_fieldmap["fields"]["fan_mode"], 16)
    CURRENT_TEMP = int(_fieldmap["fields"]["current_temperature"], 16)
    TARGET_TEMP = int(_fieldmap["fields"]["temperature"], 16)
    MODE_TO_CODE = _fieldmap["modes"]
    FAN_TO_CODE = _fieldmap["fans"]
    _MIN_C = float(_fieldmap["temperature"]["min"])
    _MAX_C = float(_fieldmap["temperature"]["max"])
    _TEMP_DIV = _fieldmap["temperature"]["divisor"]


def active_model() -> str | None:
    return _active_model


def select_model(model: str) -> bool:
    global _fieldmap, _active_model
    path = _FIELDMAP_DIR / f"{model}.json"
    if not path.exists():
        return False
    _fieldmap = json.loads(path.read_text())
    _active_model = model
    _apply_fieldmap()
    return True


def update_fieldmap(section: str, key: str, value: object) -> None:
    if _active_model is None:
        raise RuntimeError("no model selected; call select_model() first")
    _fieldmap[section][key] = value
    (_FIELDMAP_DIR / f"{_active_model}.json").write_text(json.dumps(_fieldmap, indent=2) + "\n")
    _apply_fieldmap()


def save_fieldmap(model: str, data: dict[str, Any]) -> Path:
    path = _FIELDMAP_DIR / f"{model}.json"
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def dissect(raw: bytes) -> m.DissectedPacket:
    fields: list[m.TLVField] = []
    offset = 0
    total = len(raw)
    while offset + 2 <= total:
        byte0 = raw[offset]
        byte1 = raw[offset + 1]
        type_id = (byte0 << 2) | (byte1 >> 6)
        length = (byte1 >> 4) & 3
        end = offset + 2 + length
        if end > total:
            break
        value = byte1 & 0x0F if length == 0 else int.from_bytes(raw[offset + 2 : end], "big")
        fields.append(m.TLVField(type_id, value, length))
        offset = end
    return m.DissectedPacket(fields=fields, remainder=raw[offset:])


def decode_state(packet: m.DissectedPacket) -> m.ACState:
    return state_from_raw({f.type_id: f.value for f in packet.fields})


def state_from_raw(raw: dict[int, int]) -> m.ACState:
    values: dict[str, object] = {}
    if POWER in raw:
        values["power"] = "ON" if raw[POWER] else "OFF"
    if MODE in raw:
        mode_by_code = {code: name for name, code in MODE_TO_CODE.items()}
        values["mode"] = "off" if raw.get(POWER) == 0 else mode_by_code.get(raw[MODE])
    if FAN in raw:
        fan_by_code = {code: name for name, code in FAN_TO_CODE.items()}
        values["fan_mode"] = fan_by_code.get(raw[FAN])
    if CURRENT_TEMP in raw:
        values["current_temperature"] = raw[CURRENT_TEMP] / _TEMP_DIV
    if TARGET_TEMP in raw:
        values["temperature"] = raw[TARGET_TEMP] / _TEMP_DIV
    return m.ACState(**values)  # type: ignore[arg-type]


def _clamp_temp(celsius: object) -> int:
    value = max(_MIN_C, min(_MAX_C, float(celsius)))  # type: ignore[arg-type]
    return round(value * _TEMP_DIV)


def _crc16(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _build_frame(b2: int, b3: int, b4: int, tlv: bytes) -> bytes:
    # AABB frame: [01 01 | 04 00 00 00 65 b2 b3 b4 len | <tlv> | crc16]
    body = bytes([0x04, 0x00, 0x00, 0x00, 0x65, b2, b3, b4, len(tlv)]) + tlv
    crc = _crc16(body)
    return bytes([0x01, 0x01]) + body + bytes([crc >> 8, crc & 0xFF])


def build_query(caps: bool = False) -> bytes:
    # 0x1f5=1 requests capabilities, =2 requests values; header [1,1,2,2,1].
    return _build_frame(2, 2, 1, m.TLVField.of(0x1F5, 1 if caps else 2).encode())


def frame_tlv(payload: bytes) -> m.DissectedPacket | None:
    # A device (fromDevice) TLV frame: 04 00 00 00 {87|a7} 02 {01|04} seq len <tlv> crc.
    b = payload
    if (
        len(b) >= 13
        and b[2] == 0x04
        and b[3] == 0x00
        and b[4] == 0x00
        and b[5] == 0x00
        and b[6] in (0x87, 0xA7)
        and b[7] == 0x02
        and b[8] in (0x01, 0x04)
        and b[10] == len(b) - 13
    ):
        return dissect(b[11 : len(b) - 2])
    return None


def encode_command(values: dict[str, object]) -> bytes:
    fields: list[m.TLVField] = []
    for name, value in values.items():
        if name == "power":
            fields.append(m.TLVField.of(POWER, 1 if value in _ON else 0))
        elif name == "mode":
            if value == "off":
                fields.append(m.TLVField.of(POWER, 0))
            else:
                fields.append(m.TLVField.of(POWER, 1))
                fields.append(m.TLVField.of(MODE, MODE_TO_CODE[str(value)]))
        elif name == "fan_mode":
            fields.append(m.TLVField.of(FAN, FAN_TO_CODE[str(value)]))
        elif name == "temperature":
            fields.append(m.TLVField.of(TARGET_TEMP, _clamp_temp(value)))
        else:
            raise KeyError(f"unknown AC field: {name}")
    return m.DissectedPacket(fields=fields).rebuild()


def build_command(values: dict[str, object]) -> bytes:
    # command frame uses header [1,1,2,1,1]
    return _build_frame(2, 1, 1, encode_command(values))


# --- ThinQ2 provisioning responses ---

_RESULT_OK = "0000"


def route(api_host: str, https_advertise: int, mqtt_host: str, mqtts_advertise: int) -> dict[str, object]:
    return {
        "resultCode": _RESULT_OK,
        "result": {
            "apiServer": f"https://{api_host}:{https_advertise}",
            "mqttServer": f"ssl://{mqtt_host}:{mqtts_advertise}",
        },
    }


def cert_response(pem: bytes) -> dict[str, object]:
    return {"resultCode": _RESULT_OK, "result": {"certificatePem": pem.decode()}}


def deploy(device_id: str, mid: int, cmd: str = "completeProvisioning") -> dict[str, object]:
    # Echo the phase the device asked for (preDeploy, then deploy); it drives the
    # sequence itself. mid is a fresh id — the device doesn't require it to match
    # its request.
    provisioning_type = cmd
    return {
        "did": device_id,
        "mid": mid,
        "cmd": "completeProvisioning",
        "type": 0,
        "data": {
            "result": 0,
            "host": "message",
            "appInfo": {
                "host": "message",
                "publication": {
                    "message": f"clip/message/devices/{device_id}",
                    "provisioning": f"clip/provisioning/devices/{device_id}",
                },
            },
            "provisioningType": provisioning_type,
            "deployInterval": 600,
        },
    }


# --- Certificate management ---

_VALID_FROM = datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC)
_VALID_TO = datetime.datetime(2036, 6, 1, tzinfo=datetime.UTC)
_ca: tuple[x509.Certificate, rsa.RSAPrivateKey] | None = None


def configure(ca_cert: str, ca_key: str) -> None:
    global _ca
    cert = x509.load_pem_x509_certificate(Path(ca_cert).read_bytes())
    key = serialization.load_pem_private_key(Path(ca_key).read_bytes(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TypeError("CA key is not RSA")
    _ca = (cert, key)


def _require_ca() -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    if _ca is None:
        raise RuntimeError("CA not loaded; call configure(ca_cert, ca_key)")
    return _ca


def _pem(cert: x509.Certificate, key: rsa.RSAPrivateKey) -> m.Certificate:
    return m.Certificate(
        cert.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ),
    )


def ca() -> m.Certificate:
    cert, key = _require_ca()
    return _pem(cert, key)


def _sign(
    subject: x509.Name,
    public_key: CertificatePublicKeyTypes,
    extensions: Sequence[x509.ExtensionType] = (),
) -> x509.Certificate:
    ca_cert, ca_key = _require_ca()
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(_VALID_FROM)
        .not_valid_after(_VALID_TO)
    )
    for extension in extensions:
        builder = builder.add_extension(extension, critical=False)
    return builder.sign(ca_key, hashes.SHA256())


def _tlv(data: bytes, i: int) -> tuple[int, int]:
    length = data[i + 1]
    if length & 0x80:
        n = length & 0x7F
        length = int.from_bytes(data[i + 2 : i + 2 + n], "big")
        start = i + 2 + n
    else:
        start = i + 2
    return start, start + length  # value start, element end


def _children(data: bytes, start: int, end: int) -> list[tuple[int, int]]:
    spans = []
    i = start
    while i < end:
        _, elem_end = _tlv(data, i)
        spans.append((i, elem_end))
        i = elem_end
    return spans


def _csr_public_key_der(csr_der: bytes) -> bytes:
    # CertificationRequest ::= SEQUENCE { info, signatureAlgorithm, signature }
    # info ::= SEQUENCE { version, subject, subjectPublicKeyInfo, [0] attributes }
    outer_start, outer_end = _tlv(csr_der, 0)
    info_start, info_end = _children(csr_der, outer_start, outer_end)[0]
    info_val_start, _ = _tlv(csr_der, info_start)
    spki_start, spki_end = _children(csr_der, info_val_start, info_end)[2]
    return csr_der[spki_start:spki_end]


def _pem_to_der(pem: bytes) -> bytes:
    if b"-----BEGIN" not in pem:
        return pem
    body = b"".join(line for line in pem.splitlines() if line and not line.startswith(b"-----"))
    return base64.b64decode(body)


def sign_device_csr(csr_pem: bytes, device_id: str) -> bytes:
    # The device's CSR is non-canonical DER that strict parsers reject. We don't
    # need to validate its self-signature (it's enrolling on our own network) —
    # extract the public key ahead of the offending field and sign a cert for it.
    spki = _csr_public_key_der(_pem_to_der(csr_pem))
    public_key = cast(CertificatePublicKeyTypes, serialization.load_der_public_key(spki))
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, device_id)])
    cert = _sign(subject, public_key, [x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH])])
    return cert.public_bytes(serialization.Encoding.PEM)
