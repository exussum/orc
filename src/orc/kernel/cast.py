import builtins
import re
from collections.abc import Mapping
from datetime import time
from types import MappingProxyType
from typing import Any

from orc_engine import cast as engine_cast

from orc import model as m

_NO_OBJECTS: Mapping[str, Any] = MappingProxyType({})
_ERR_TIME = "Invalid time {!r}: expected HH:MM, 'sunrise', or 'sunset'"
# "device" plugins are invoked per-device from the /device grid (via /api/run?device=…);
# they render no button and are not auto-invoked, unlike the other sections.
_VALID_SECTIONS = frozenset({"scene", "system", "device"})
_YOUTUBE_ID = re.compile(r"[0-9A-Za-z_-]{11}")
_ERR_PARAMS = "Invalid parameter {}={!r}"
_ERR_STATE = (
    "Invalid state {!r}: expected one of 'on', 'off', 'stop', 'pause', 'resume', an integer, an 11-character YouTube ID, or mode:fan:temp"
)
_ERR_AC_COMMAND = "Invalid AC command {!r}: expected mode:fan:temp with mode one of 'cool', 'fan_only', 'econ', 'dry', e.g. cool:low:75"


# scalar() calls a caster as (value, objects) and each() as (value,), so casters
# used by both take a trailing `objects` with a default.
def devices(value: str, objects: Mapping[str, Any] = _NO_OBJECTS) -> m.Devices:
    return resolve_device(value, objects["device"].enums)


def device(value: str, objects: Mapping[str, Any] = _NO_OBJECTS) -> m.DeviceEnum:
    return resolve_device(value, objects["device"].enums).one()


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
    if value in (m.SUNRISE, m.SUNSET):
        return value
    try:
        return engine_cast.clock(value)
    except ValueError:
        raise ValueError(_ERR_TIME.format(value)) from None


def section(value: str | None) -> str | None:
    if value is None or value in _VALID_SECTIONS:
        return value
    raise ValueError(_ERR_PARAMS.format("section", value))


def resolve_device(value: str, devices: Mapping[str, type[m.DeviceEnum]]) -> m.Devices:
    try:
        return m.Devices(eval(value, dict(devices)))  # nosemgrep: python.lang.security.audit.eval-detected.eval-detected
    except NameError as exc:
        raise ValueError(f"{exc} — device types must be defined and sealed first") from None
    except AttributeError:
        type_name, _, member = value.partition(".")
        options = sorted(devices[type_name].__members__) if type_name in devices else []
        raise ValueError(f"Unknown {type_name} device {member!r}: expected one of {options}") from None
    except SyntaxError as exc:
        raise ValueError(str(exc)) from None
