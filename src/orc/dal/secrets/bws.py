import base64
import hashlib
import hmac as _hmac
import json
import os
from collections.abc import Callable
from typing import Any

import requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import hmac as crypto_hmac
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand
from cryptography.hazmat.primitives.padding import PKCS7

import orc
from orc import model as m

REQUIRED_SECRETS: dict[str, Callable[[str], Any]] = {}

_IDENTITY_URL = "https://identity.bitwarden.com"
_API_URL = "https://api.bitwarden.com"


def fetch_secrets() -> m.Secrets:
    raw_token = _get_url_value(os.environ["BWS_ACCESS_TOKEN"])

    access_token_id, client_secret, enc_key_raw = _parse_access_token(raw_token)
    derived_key = _derive_key(enc_key_raw)

    response = requests.post(
        f"{_IDENTITY_URL}/connect/token",
        data={
            "scope": "api.secrets",
            "client_id": access_token_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        },
        timeout=orc.config.settings.http_timeout,
    )
    response.raise_for_status()
    auth = response.json()

    jwt = auth["access_token"]
    org_key = base64.b64decode(json.loads(_decrypt_enc_string(auth["encrypted_payload"], derived_key))["encryptionKey"])
    payload_b64 = jwt.split(".")[1]
    jwt_payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)))
    org_id = jwt_payload["organization"]

    list_resp = _api_get(f"{_API_URL}/organizations/{org_id}/secrets", jwt)
    ids = [s["id"] for s in list_resp["secrets"]]

    secrets_resp = _api_post(f"{_API_URL}/secrets/get-by-ids", jwt, {"ids": ids})
    secrets = {_decrypt_enc_string(s["key"], org_key): _decrypt_enc_string(s["value"], org_key) for s in secrets_resp["data"]}

    return m.Secrets(
        market_holidays_url=secrets.pop("MARKET_HOLIDAYS_URL", ""),
        mqtt_user=secrets.pop("MQTT_USER", ""),
        mqtt_password=secrets.pop("MQTT_PASSWORD", ""),
        vapid_private_key=secrets.pop("VAPID_PRIVATE_KEY", ""),
        find_hub_username=secrets.pop("FIND_HUB_USERNAME", ""),
        find_hub_aas_token=secrets.pop("FIND_HUB_AAS_TOKEN", ""),
        find_hub_android_id=secrets.pop("FIND_HUB_ANDROID_ID", ""),
        find_hub_owner_key=secrets.pop("FIND_HUB_OWNER_KEY", ""),
        other=secrets,
    )


def _parse_access_token(token: str) -> tuple[str, str, bytes]:
    token_part, enc_key_b64 = token.rsplit(":", 1)
    parts = token_part.split(".")
    return parts[1], parts[2], base64.b64decode(enc_key_b64)


def _derive_key(enc_key_raw: bytes) -> bytes:
    prk = _hmac.new(b"bitwarden-accesstoken", enc_key_raw, hashlib.sha256).digest()
    return HKDFExpand(algorithm=hashes.SHA256(), length=64, info=b"sm-access-token").derive(prk)


def _decrypt_enc_string(enc_string: str, key_64: bytes) -> str:
    _, rest = enc_string.split(".", 1)
    iv_b64, ct_b64, mac_b64 = rest.split("|")

    enc_key, mac_key = key_64[:32], key_64[32:]
    iv = base64.b64decode(iv_b64)
    ct = base64.b64decode(ct_b64)
    mac = base64.b64decode(mac_b64)

    h = crypto_hmac.HMAC(mac_key, hashes.SHA256())
    h.update(iv + ct)
    h.verify(mac)

    decryptor = Cipher(algorithms.AES(enc_key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(ct) + decryptor.finalize()
    unpadder = PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")


def _api_get(url: str, token: str) -> Any:
    response = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=orc.config.settings.http_timeout)
    response.raise_for_status()
    return response.json()


def _api_post(url: str, token: str, body: dict[str, Any]) -> Any:
    response = requests.post(url, json=body, headers={"Authorization": f"Bearer {token}"}, timeout=orc.config.settings.http_timeout)
    response.raise_for_status()
    return response.json()


def _get_url_value(url: str) -> str:
    response = requests.get(url, timeout=orc.config.settings.http_timeout)
    response.raise_for_status()
    return response.text.partition("\n")[0].strip()
