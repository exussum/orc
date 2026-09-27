import base64
import builtins
import importlib
import re
from collections.abc import Callable, Mapping
from datetime import time
from types import MappingProxyType, ModuleType
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes

from orc import model as m
from orc.security import safe_eval

_NO_OBJECTS: Mapping[str, Any] = MappingProxyType({})
# "device" plugins are invoked per-device from the /device grid (via /api/run?device=…);
# they render no button and are not auto-invoked, unlike the other sections.
_VALID_SECTIONS = frozenset({"scene", "system", "device"})
_YOUTUBE_ID = re.compile(r"[0-9A-Za-z_-]{11}")
_KEY32 = re.compile(r"[A-Za-z0-9_-]{43}=?")
_HEX32 = re.compile(r"[0-9A-Fa-f]{64}")
_FQDN_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")  # 1-63 chars, no leading/trailing hyphen
_ERR_PARAMS = "Invalid parameter {}={!r}"
_ERR_STATE = (
    "Invalid state {!r}: expected one of 'on', 'off', 'stop', 'pause', 'resume', an integer, an 11-character YouTube ID, or mode:fan:temp"
)
_ERR_AC_COMMAND = "Invalid AC command {!r}: expected mode:fan:temp with mode one of 'cool', 'fan_only', 'econ', 'dry', e.g. cool:low:75"
_ERR_TIME = "Invalid time {!r}: expected HH:MM"
_ERR_MODULE = "Cannot load module {!r}: {}. Expected an importable module like 'orc.dal.mqtt.stub'."
_ERR_FUNCTION = "Cannot load function {!r}: {}. Expected a fully qualified callable like 'orc.plugins.my_plugin'."


# scalar() calls a caster as (value, objects) and each() as (value,), so casters
# used by both take a trailing `objects` with a default.
def devices(value: str, objects: Mapping[str, Any] = _NO_OBJECTS) -> m.Devices:
    return resolve_device(value, objects["device"].enums)


def device(value: str, objects: Mapping[str, Any] = _NO_OBJECTS) -> m.DeviceEnum:
    return resolve_device(value, objects["device"].enums).one()


def module(value: str, objects: Mapping[str, Any] = _NO_OBJECTS) -> ModuleType:
    try:
        return importlib.import_module(value)  # nosemgrep: non-literal-import
    except Exception as exc:
        raise ValueError(_ERR_MODULE.format(value, exc)) from exc


def float(value: str, objects: Mapping[str, Any] = _NO_OBJECTS) -> builtins.float:
    return builtins.float(value)


def int(value: str, objects: Mapping[str, Any] = _NO_OBJECTS) -> builtins.int:
    return builtins.int(value)


def bool(value: str, objects: Mapping[str, Any] = _NO_OBJECTS) -> builtins.bool:
    if value in ("True", "False"):
        return value == "True"
    raise ValueError(_ERR_PARAMS.format("bool", value))


def fqdn(value: str, objects: Mapping[str, Any] = _NO_OBJECTS) -> str:
    labels = value.split(".")
    if len(value) <= 253 and len(labels) >= 2 and all(map(_FQDN_LABEL.fullmatch, labels)):
        return value
    raise ValueError(_ERR_PARAMS.format("fqdn", value))


def state(value: str) -> Any:
    if value in (m.ON, m.OFF, m.STOP, m.PAUSE, m.RESUME):
        return value
    elif ":" in value:
        try:
            mode, fan, temp = value.split(":")
            return m.AcCommand(m.AcMode(mode), fan, builtins.int(temp))
        except ValueError:
            raise ValueError(_ERR_AC_COMMAND.format(value)) from None
    elif _YOUTUBE_ID.fullmatch(value):
        return m.YouTubeId(value)
    elif value.isdigit():
        return builtins.int(value)
    raise ValueError(_ERR_STATE.format(value))


def when(value: str) -> time | str:
    return m.resolve_time(value)


def clock(value: str) -> time:
    parsed = m.resolve_time(value)
    if isinstance(parsed, time):
        return parsed
    raise ValueError(_ERR_TIME.format(value))


def section(value: str | None) -> str | None:
    if value is None or value in _VALID_SECTIONS:
        return value
    raise ValueError(_ERR_PARAMS.format("section", value))


def nonblank(value: str) -> str:
    if value:
        return value
    raise ValueError(_ERR_PARAMS.format("nonblank", value))


def url(value: str) -> str:
    parts = urlparse(value)
    if parts.scheme in ("http", "https") and parts.netloc:
        return value
    raise ValueError(_ERR_PARAMS.format("url", value))


def uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError:
        raise ValueError(_ERR_PARAMS.format("uuid", value)) from None


def key32(value: str) -> bytes:
    if _KEY32.fullmatch(value):
        return base64.urlsafe_b64decode(value.rstrip("=") + "=")
    raise ValueError(_ERR_PARAMS.format("key32", value))


def hex32(value: str) -> bytes:
    if _HEX32.fullmatch(value):
        return bytes.fromhex(value)
    raise ValueError(_ERR_PARAMS.format("hex32", value))


def pem_cert(value: str) -> x509.Certificate:
    try:
        return x509.load_pem_x509_certificate(value.encode())
    except ValueError:
        raise ValueError(_ERR_PARAMS.format("pem_cert", value)) from None


def pem_key(value: str) -> PrivateKeyTypes:
    try:
        return serialization.load_pem_private_key(value.encode(), None)
    except ValueError, TypeError:
        raise ValueError(_ERR_PARAMS.format("pem_key", value)) from None


def resolve_function(value: str) -> Callable[..., Any]:
    try:
        module_path, fn_name = value.rsplit(".", 1)
        return getattr(importlib.import_module(module_path), fn_name)  # nosemgrep: non-literal-import
    except Exception as exc:
        raise ValueError(_ERR_FUNCTION.format(value, exc)) from exc


def resolve_device(value: str, devices: Mapping[str, type[m.DeviceEnum]]) -> m.Devices:
    try:
        return m.Devices(safe_eval(value, dict(devices)))
    except NameError as exc:
        raise ValueError(f"{exc} — device types must be defined and sealed first") from None
    except AttributeError:
        type_name, _, member = value.partition(".")
        options = sorted(devices[type_name].__members__) if type_name in devices else []
        raise ValueError(f"Unknown {type_name} device {member!r}: expected one of {options}") from None
    except SyntaxError as exc:
        raise ValueError(str(exc)) from None
