"""Generate certs for the provisioning TLS listener and broker.

Writes, under certs/lg_ac/: ca.{crt,key}, server-ca.{crt,key} (CA-signed),
server-selfsigned.{crt,key}. Server certs carry SAN, KeyUsage, and
ExtendedKeyUsage(serverAuth, clientAuth) so picky embedded TLS clients accept
them. The server SAN includes the FQDN and the mqtt_host IP the device connects
to, both read from the plugin config; run from the orc root.
"""

import datetime
import ipaddress
import socket
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CN = "common.lgthinq.com"
CERTS = Path("certs/lg_ac")
_CONFIG = Path("src/plugins/orc_extras/lg_ac.orc")
_FROM = datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC)
_TO = datetime.datetime(2036, 6, 1, tzinfo=datetime.UTC)


def _key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _base(subject: x509.Name, issuer: x509.Name, public_key: rsa.RSAPublicKey) -> x509.CertificateBuilder:
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(_FROM)
        .not_valid_after(_TO)
    )


def _write(stem: str, cert: x509.Certificate, key: rsa.RSAPrivateKey) -> None:
    (CERTS / f"{stem}.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (CERTS / f"{stem}.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )


def _setting(key: str) -> str:
    for line in _CONFIG.read_text().splitlines():
        parts = line.split()
        if parts[:2] == ["setting", key]:
            return parts[2]
    raise SystemExit(f"no 'setting {key}' line in {_CONFIG}")


def _san(fqdn: str, mqtt_ip: str) -> x509.SubjectAlternativeName:
    entries: list[x509.GeneralName] = [x509.DNSName(CN), x509.DNSName(fqdn)]
    entries.append(x509.IPAddress(ipaddress.ip_address(mqtt_ip)))
    return x509.SubjectAlternativeName(entries)


def _server(
    self_signed: bool,
    fqdn: str,
    mqtt_ip: str,
    ca_cert: x509.Certificate | None = None,
    ca_key: rsa.RSAPrivateKey | None = None,
) -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    key = _key()
    issuer = _name(CN) if self_signed else ca_cert.subject  # type: ignore[union-attr]
    signer = key if self_signed else ca_key
    cert = (
        _base(_name(CN), issuer, key.public_key())
        .add_extension(_san(fqdn, mqtt_ip), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
        .sign(signer, hashes.SHA256())  # type: ignore[arg-type]
    )
    return cert, key


def main() -> None:
    fqdn = _setting("fqdn")
    if fqdn.endswith(".example"):
        raise SystemExit(f"set 'fqdn' in {_CONFIG} to this server's real FQDN before generating certs (still the .example placeholder)")
    mqtt_ip = socket.gethostbyname(fqdn)  # the device connects to the broker by IP
    CERTS.mkdir(parents=True, exist_ok=True)

    ca_key = _key()
    ca_cert = (
        _base(_name("lg_ac-ca"), _name("lg_ac-ca"), ca_key.public_key())
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    _write("ca", ca_cert, ca_key)

    ca_signed, ca_signed_key = _server(self_signed=False, fqdn=fqdn, mqtt_ip=mqtt_ip, ca_cert=ca_cert, ca_key=ca_key)
    _write("server-ca", ca_signed, ca_signed_key)

    self_signed, self_signed_key = _server(self_signed=True, fqdn=fqdn, mqtt_ip=mqtt_ip)
    _write("server-selfsigned", self_signed, self_signed_key)

    print(f"wrote {CERTS}/ca.crt, server-ca.crt, server-selfsigned.crt (+ keys)")


if __name__ == "__main__":
    main()
