"""A tag's identity key from the Find Hub account, over the Nova API the Android app uses.

The device list reply is a protobuf walked by field number: DevicesList.deviceMetadata(2)
→ userDefinedDeviceName(5), information(4).deviceRegistration(1) → encryptedUserSecrets(19)
.encryptedIdentityKey(1), fastPairModelId(21), pairDate(23). The key is AES-encrypted with
the account's owner key, CBC with a 16-byte IV at 48 bytes or GCM with a 12-byte nonce at 60.
"""

from collections.abc import Callable
from typing import Any
from uuid import uuid4

import gpsoauth
import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from orc_engine import cast

from orc import model as m

REQUIRED_SECRETS: dict[str, Callable[[str], Any]] = {
    "find_hub_username": cast.nonblank,
    "find_hub_aas_token": cast.nonblank,
    "find_hub_android_id": cast.nonblank,
    "find_hub_owner_key": cast.hex32,
}

_NOVA_URL = "https://android.googleapis.com/nova/nbe_list_devices"
_ADM_SCOPE = "oauth2:https://www.googleapis.com/auth/android_device_manager"
_ADM_APP = "com.google.android.apps.adm"
_ADM_SIGNATURE = "38918a453d07199354f8b19af05ec6562ced5788"
_MCU_MODEL_ID = "003200"

type Field = tuple[int, int | bytes]


def identity_key(device: str, secrets: m.Secrets, timeout: int) -> m.BleKey:
    auth = gpsoauth.perform_oauth(
        secrets.find_hub_username,
        secrets.find_hub_aas_token,
        secrets.find_hub_android_id,
        service=_ADM_SCOPE,
        app=_ADM_APP,
        client_sig=_ADM_SIGNATURE,
    )
    if "Auth" not in auth:
        raise PermissionError(f"Find Hub token refused: {auth.get('Error', 'no error given')}")
    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Authorization": f"Bearer {auth['Auth']}",
        "Accept-Language": "en-US",
        "User-Agent": "fmd/20006320; gzip",
    }
    reply = requests.post(_NOVA_URL, data=_list_request(), headers=headers, timeout=timeout)
    reply.raise_for_status()
    return registration(device, reply.content, bytes.fromhex(secrets.find_hub_owner_key))


def registration(device: str, devices_list: bytes, owner_key: bytes) -> m.BleKey:
    for metadata in _submessages(devices_list, 2):
        name = _first(metadata, 5)
        if not isinstance(name, bytes) or name.decode() != device:
            continue
        fields = _parse(_message(_first(_parse(_message(_first(metadata, 4))), 1)))
        encrypted = _first(_parse(_message(_first(fields, 19))), 1)
        pair_date = _first(fields, 23)
        model_id = _first(fields, 21)
        if not isinstance(encrypted, bytes) or not isinstance(pair_date, int):
            raise LookupError(f"Find Hub device {device!r} has no identity key registration")
        if isinstance(model_id, bytes) and model_id.decode() == _MCU_MODEL_ID:
            encrypted = bytes(b ^ 0xFF for b in encrypted)
        return m.BleKey(_decrypt(owner_key, encrypted), pair_date)
    raise LookupError(f"no Find Hub device named {device!r}")


def _list_request() -> bytes:
    request_id = uuid4().hex.encode()
    payload = b"\x08\x02\x1a" + bytes([len(request_id)]) + request_id
    return b"\x0a" + bytes([len(payload)]) + payload


def _decrypt(owner_key: bytes, encrypted: bytes) -> bytes:
    if len(encrypted) == 48:
        decryptor = Cipher(algorithms.AES(owner_key), modes.CBC(encrypted[:16])).decryptor()
        return decryptor.update(encrypted[16:]) + decryptor.finalize()
    elif len(encrypted) == 60:
        return AESGCM(owner_key).decrypt(encrypted[:12], encrypted[12:], None)
    raise ValueError(f"unexpected identity key length {len(encrypted)}")


def _parse(buf: bytes) -> list[Field]:
    fields: list[Field] = []
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _varint(buf, i)
            fields.append((number, value))
        elif wire == 2:
            length, i = _varint(buf, i)
            fields.append((number, buf[i : i + length]))
            i += length
        elif wire == 1:
            i += 8
        elif wire == 5:
            i += 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")
    return fields


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        byte = buf[i]
        i += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, i
        shift += 7


def _submessages(buf: bytes, number: int) -> list[list[Field]]:
    return [_parse(value) for n, value in _parse(buf) if n == number and isinstance(value, bytes)]


def _first(fields: list[Field], number: int) -> int | bytes | None:
    return next((value for n, value in fields if n == number), None)


def _message(value: int | bytes | None) -> bytes:
    if not isinstance(value, bytes):
        raise LookupError("Find Hub registration is missing an expected field")
    return value
