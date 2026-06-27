import base64
import json

import broadlink as bl

from orc.dal._decorators import requires_enabled


def _connect(device):
    dev = bl.hello(device.value)
    dev.auth()
    return dev


def _codes(path):
    with open(path) as f:
        return json.load(f)


def _send(dev, code_b64):
    dev.send_data(base64.b64decode(code_b64))


@requires_enabled(None)
def set_ac(device, codes_file, mode, fan, temp):
    _send(_connect(device), _codes(codes_file)["commands"][mode][fan][str(temp)])


@requires_enabled(None)
def tv_on(device, codes_file):
    _send(_connect(device), _codes(codes_file)["commands"]["on"])


@requires_enabled(None)
def ac_off(device, codes_file):
    _send(_connect(device), _codes(codes_file)["commands"]["off"])
