import base64
import json

import broadlink as bl

from orc.dal._decorators import requires_enabled


@requires_enabled(None)
def set_ac(device, codes_file, mode, fan, temp):
    cmds = _codes(codes_file)["ac"]["commands"][mode]
    if mode == "fan_only":
        code = cmds[fan]
    elif mode == "dry":
        code = cmds[str(temp)]
    else:
        code = cmds[fan][str(temp)]
    _send(_connect(device), code)


@requires_enabled(None)
def tv_toggle(device, codes_file):
    _send(_connect(device), _codes(codes_file)["tv"]["commands"]["toggle"])


@requires_enabled(None)
def ac_off(device, codes_file):
    _send(_connect(device), _codes(codes_file)["ac"]["commands"]["off"])


def _connect(device):
    dev = bl.hello(device.value)
    for attempt in range(4):
        if dev.auth():
            return dev
        if attempt == 3:
            raise ConnectionError(f"Broadlink auth failed for {device.value}")
    return dev


def _codes(path):
    with open(path) as f:
        return json.load(f)


def _send(dev, code_b64):
    dev.send_data(base64.b64decode(code_b64))
