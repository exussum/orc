import base64
import json
from typing import Any

import broadlink as bl

from orc.model import DeviceEnum


def tv_toggle(device: DeviceEnum, codes_file: str) -> None:
    _send(_connect(device), _codes(codes_file)["tv"]["commands"]["toggle"])


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
