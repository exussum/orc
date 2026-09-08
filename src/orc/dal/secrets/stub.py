import datetime
import functools

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from orc import model as m
from orc.dal import warn_stub

warn_stub("secrets")


@functools.cache
def _cert_pair(stem: str) -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"stub {stem}")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode(), key_pem.decode()


class _StubSecrets(dict[str, str]):
    def __getitem__(self, key: str) -> str:
        if key.endswith("_CERT"):
            return _cert_pair(key.removesuffix("_CERT"))[0]
        elif key.endswith("_KEY"):
            return _cert_pair(key.removesuffix("_KEY"))[1]
        return f"secret_{key}"


def fetch_secrets() -> m.Secrets:
    return m.Secrets(
        hubitat_access_token="secret_hubitat_access_token",
        market_holidays_url="secret_market_holidays_url",
        mqtt_user="secret_mqtt_user",
        mqtt_password="secret_mqtt_password",
        other=_StubSecrets(),
    )
