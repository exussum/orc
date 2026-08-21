import base64
import json
import time
from typing import Any

import broadlink as bl

from orc.model import DeviceEnum

_STEP_DELAY = 0.5

_last_temp: dict[DeviceEnum, int] = {}


def set_ac(device: DeviceEnum, codes_file: str, mode: str, fan: str, temp: int) -> None:
    cmds = _codes(codes_file)["ac"]["commands"][mode]
    dev = _connect(device)

    if mode == "fan_only":
        _send(dev, cmds[fan])
        return

    if mode != "dry":
        _send(dev, cmds["fan"][fan])

    current = _last_temp.get(device, temp)
    step = cmds["temp"]["up"] if temp > current else cmds["temp"]["down"]
    for _ in range(abs(temp - current)):
        _send(dev, step)
        time.sleep(_STEP_DELAY)
    _last_temp[device] = temp


def tv_toggle(device: DeviceEnum, codes_file: str) -> None:
    _send(_connect(device), _codes(codes_file)["tv"]["commands"]["toggle"])


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
    dev.send_data(base64.b64decode(code_b64))
