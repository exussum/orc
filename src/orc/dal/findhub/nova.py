# Copyright (C) 2025-2026 exussum
# Portions derived from GoogleFindMyTools, Copyright (C) 2024 Leon Böttger,
# GPL-3.0: https://github.com/leonboe1/GoogleFindMyTools.
#
# This file is part of orc.
#
# orc is free software: you can redistribute it and/or modify it under the terms
# of the GNU General Public License version 3 as published by the Free Software
# Foundation.
#
# orc is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
# without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
# PURPOSE. See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# orc. If not, see <https://www.gnu.org/licenses/>.

from collections.abc import Callable
from typing import Any
from uuid import uuid4

import gpsoauth
import requests
from google.protobuf import descriptor_pb2, message_factory
from orc_engine import cast

import orc
from orc import model as m
from orc import security

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
_SPOT_DEVICE = 2
_SCHEMA: dict[str, list[tuple[str, int, str]]] = {
    "DevicesListRequest": [("deviceListRequestPayload", 1, "DeviceListRequestPayload")],
    "DeviceListRequestPayload": [("type", 1, "int64"), ("id", 3, "string")],
    "DevicesList": [("deviceMetadata", 2, "repeated DeviceMetadata")],
    "DeviceMetadata": [("information", 4, "DeviceInformation"), ("userDefinedDeviceName", 5, "string")],
    "DeviceInformation": [("deviceRegistration", 1, "DeviceRegistration")],
    "DeviceRegistration": [
        ("encryptedUserSecrets", 19, "EncryptedUserSecrets"),
        ("fastPairModelId", 21, "string"),
        ("pairDate", 23, "int64"),
    ],
    "EncryptedUserSecrets": [("encryptedIdentityKey", 1, "bytes")],
}
_SCALARS = {
    "int64": descriptor_pb2.FieldDescriptorProto.TYPE_INT64,
    "string": descriptor_pb2.FieldDescriptorProto.TYPE_STRING,
    "bytes": descriptor_pb2.FieldDescriptorProto.TYPE_BYTES,
}


def _messages() -> dict[str, Any]:
    file = descriptor_pb2.FileDescriptorProto(name="nova.proto", package="nova", syntax="proto3")
    for name, fields in _SCHEMA.items():
        message = file.message_type.add(name=name)
        for field_name, number, kind in fields:
            repeated, _, kind = kind.rpartition(" ")
            field = message.field.add(name=field_name, number=number)
            field.label = field.LABEL_REPEATED if repeated else field.LABEL_OPTIONAL
            if kind in _SCALARS:
                field.type = _SCALARS[kind]
            else:
                field.type = field.TYPE_MESSAGE
                field.type_name = f".nova.{kind}"
    return {name.removeprefix("nova."): cls for name, cls in message_factory.GetMessages([file]).items()}


_MESSAGES = _messages()


def identity_key(device: str) -> m.BleKey:
    return registration(device, devices_list(), bytes.fromhex(orc.config.secrets.find_hub_owner_key))


def devices_list() -> bytes:
    secrets = orc.config.secrets
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
    request = _MESSAGES["DevicesListRequest"](deviceListRequestPayload={"type": _SPOT_DEVICE, "id": uuid4().hex})
    reply = requests.post(_NOVA_URL, data=request.SerializeToString(), headers=headers, timeout=orc.config.settings.http_timeout)
    reply.raise_for_status()
    return reply.content


def registration(device: str, devices_list: bytes, owner_key: bytes) -> m.BleKey:
    devices = _MESSAGES["DevicesList"].FromString(devices_list)
    for metadata in devices.deviceMetadata:
        if metadata.userDefinedDeviceName != device:
            continue
        fields = metadata.information.deviceRegistration
        encrypted = fields.encryptedUserSecrets.encryptedIdentityKey
        if not encrypted or not fields.pairDate:
            raise LookupError(f"Find Hub device {device!r} has no identity key registration")
        inverted = fields.fastPairModelId == _MCU_MODEL_ID
        return m.BleKey(security.fmdn_identity_key(owner_key, encrypted, inverted=inverted), fields.pairDate)
    names = ", ".join(sorted(metadata.userDefinedDeviceName for metadata in devices.deviceMetadata))
    raise LookupError(f"no Find Hub device named {device!r}; the account has: {names}")
