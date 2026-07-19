import base64
import json
from typing import TYPE_CHECKING, Any

import broadlink as bl

from orc.dal._decorators import requires_enabled

if TYPE_CHECKING:
    from orc.model import DeviceEnum


@requires_enabled(None)
def set_ac(device: DeviceEnum, codes_file: str, mode: str, fan: str, temp: int) -> None:
    cmds = _codes(codes_file)["ac"]["commands"][mode]
    if mode == "fan_only":
        code = cmds[fan]
    elif mode == "dry":
        code = cmds[str(temp)]
    else:
        code = cmds[fan][str(temp)]
    _send(_connect(device), code)


@requires_enabled(None)
def tv_toggle(device: DeviceEnum, codes_file: str) -> None:
    _send(_connect(device), _codes(codes_file)["tv"]["commands"]["toggle"])


@requires_enabled(None)
def ac_off(device: DeviceEnum, codes_file: str) -> None:
    _send(_connect(device), _codes(codes_file)["ac"]["commands"]["off"])


def _connect(device: DeviceEnum) -> Any:
    dev = bl.hello(device.value)
    for _ in range(4):
        if dev.auth():
            return dev
    raise ConnectionError(f"Broadlink auth failed for {device.value}")


def _codes(path: str) -> Any:
    with open(path) as f:
        return json.load(f)


def _send(dev: Any, code_b64: str) -> None:
    data = bytearray(base64.b64decode(code_b64))
    data[1] = 2  # repeat 2 more times = 3 total
    dev.send_data(bytes(data))
