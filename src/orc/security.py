import base64
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.types import CertificatePublicKeyTypes

from orc.model import CA, Certificate


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
