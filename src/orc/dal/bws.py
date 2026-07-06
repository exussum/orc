import base64
import hashlib
import hmac as _hmac
import json
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import hmac as crypto_hmac
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand
from cryptography.hazmat.primitives.padding import PKCS7

from orc import model as m

_IDENTITY_URL = "https://identity.bitwarden.com"
_API_URL = "https://api.bitwarden.com"


def fetch_secrets():
    raw_token = _get_url_value(os.environ["BWS_ACCESS_TOKEN"])

    access_token_id, client_secret, enc_key_raw = _parse_access_token(raw_token)
    derived_key = _derive_key(enc_key_raw)

    # Authenticate and get JWT + encrypted org key + org ID
    body = urlencode(
        {
            "scope": "api.secrets",
            "client_id": access_token_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        }
    ).encode()
    req = Request(
        f"{_IDENTITY_URL}/connect/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urlopen(req) as r:  # nosemgrep: dynamic-urllib-use-detected
        auth = json.loads(r.read())

    jwt = auth["access_token"]
    org_key = base64.b64decode(json.loads(_decrypt_enc_string(auth["encrypted_payload"], derived_key))["encryptionKey"])
    jwt_payload = json.loads(base64.b64decode(jwt.split(".")[1] + "=="))
    org_id = jwt_payload["organization"]

    # List secret IDs then fetch values
    list_resp = _api_get(f"{_API_URL}/organizations/{org_id}/secrets", jwt)
    ids = [s["id"] for s in list_resp["secrets"]]

    secrets_resp = _api_post(f"{_API_URL}/secrets/get-by-ids", jwt, {"ids": ids})
    secrets = {_decrypt_enc_string(s["key"], org_key): _decrypt_enc_string(s["value"], org_key) for s in secrets_resp["data"]}

    return m.Secrets(
        access_token="?access_token=" + secrets["HUBITAT_ACCESS_TOKEN"],
        market_holidays_url=secrets["MARKET_HOLIDAYS_URL"],
        ics_url=secrets["ICS_URL"],
        yolink_id=secrets["YOLINK_ID"],
        yolink_secret=secrets["YOLINK_SECRET"],
    )


def _parse_access_token(token):
    # Format: 0.<access_token_id>.<client_secret>:<b64_enc_key>
    token_part, enc_key_b64 = token.rsplit(":", 1)
    parts = token_part.split(".")
    return parts[1], parts[2], base64.b64decode(enc_key_b64)


def _derive_key(enc_key_raw):
    # HKDF: extract with "bitwarden-accesstoken" as salt, expand with "sm-access-token" as info
    prk = _hmac.new(b"bitwarden-accesstoken", enc_key_raw, hashlib.sha256).digest()
    return HKDFExpand(algorithm=hashes.SHA256(), length=64, info=b"sm-access-token").derive(prk)


def _decrypt_enc_string(enc_string, key_64):
    # EncString type 2: "2.<iv_b64>|<ct_b64>|<mac_b64>"
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


def _api_get(url, token):
    with urlopen(Request(url, headers={"Authorization": f"Bearer {token}"})) as r:  # nosemgrep: dynamic-urllib-use-detected
        return json.loads(r.read())


def _api_post(url, token, body):
    data = json.dumps(body).encode()
    req = Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urlopen(req) as r:  # nosemgrep: dynamic-urllib-use-detected
        return json.loads(r.read())


def _get_url_value(url):
    with urlopen(url) as response:  # nosemgrep: dynamic-urllib-use-detected
        return response.readline().decode("utf-8").strip()
