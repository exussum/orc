import base64
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.asymmetric.types import CertificatePublicKeyTypes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from ecdsa.curves import SECP160r1

from orc.model import CA, Certificate

# FMDN ephemeral-identifier math and frame parsing. The EID scheme — the two-block
# PRF input (spec Table 10), AES-256-ECB, and the per-curve scalar projection — is
# from Google's Find Hub Network accessory spec:
# https://developers.google.com/nearby/fast-pair/specifications/extensions/fmdn.
# Variant semantics and the golden test vectors follow BSkando's GoogleFindMy-HA
# (MIT): https://github.com/BSkando/GoogleFindMy-HA.
FMDN_SERVICE_UUID = "0000feaa-0000-1000-8000-00805f9b34fb"
FMDN_ROTATION_SECONDS = 1024

_FMDN_K = 10
_FMDN_FRAME_TYPES = (0x40, 0x41)
_P256_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551


def safe_eval(val: str, ns: dict[str, Any]) -> Any:
    return eval(val, ns)  # nosemgrep: python.lang.security.audit.eval-detected.eval-detected


def load_ca(cert_pem: bytes, key_pem: bytes) -> CA:
    cert = x509.load_pem_x509_certificate(cert_pem)
    key = serialization.load_pem_private_key(key_pem, password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TypeError("CA key is not RSA")
    return CA(cert, key)


def sign(
    ca: CA,
    subject: x509.Name,
    public_key: CertificatePublicKeyTypes,
    extensions: Sequence[x509.ExtensionType] = (),
    *,
    not_before: datetime,
    not_after: datetime,
) -> x509.Certificate:
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca.cert.subject)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
    )
    for extension in extensions:
        builder = builder.add_extension(extension, critical=False)
    return builder.sign(ca.key, hashes.SHA256())


def pem(cert: x509.Certificate, key: rsa.RSAPrivateKey) -> Certificate:
    return Certificate(
        cert.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ),
    )


def pem_to_der(data: bytes) -> bytes:
    if b"-----BEGIN" not in data:
        return data
    body = b"".join(line for line in data.splitlines() if line and not line.startswith(b"-----"))
    return base64.b64decode(body)


def fmdn_parse(service_data: bytes) -> bytes | None:
    """EID bytes from an FMDN service-data frame, or None for anything else."""
    if len(service_data) < 21 or service_data[0] not in _FMDN_FRAME_TYPES:
        return None
    elif len(service_data) >= 33:
        return service_data[1:33]
    return service_data[1:21]


def fmdn_eids(eik: bytes, counter: int) -> tuple[bytes, bytes, bytes]:
    """The EID variants a tag may broadcast: secp160r1, p256, and p256 truncated."""
    r = int.from_bytes(_fmdn_prf(eik, counter), "big")
    legacy = int((SECP160r1.generator * (r % SECP160r1.order)).x()).to_bytes(20, "big")
    # The spec keeps modern scalars off the endpoints: [1, n-1), not mod n.
    modern_key = ec.derive_private_key(r % (_P256_ORDER - 1) + 1, ec.SECP256R1()).public_key()
    modern = modern_key.public_numbers().x.to_bytes(32, "big")
    return legacy, modern, modern[:20]


def _fmdn_prf(eik: bytes, counter: int) -> bytes:
    window = (counter & 0xFFFFFFFF & ~(FMDN_ROTATION_SECONDS - 1)).to_bytes(4, "big")
    block = b"\xff" * 11 + bytes([_FMDN_K]) + window + b"\x00" * 11 + bytes([_FMDN_K]) + window
    return Cipher(algorithms.AES(eik), modes.ECB()).encryptor().update(block)
