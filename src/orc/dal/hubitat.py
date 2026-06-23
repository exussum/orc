import requests

from orc import config
from orc import model as m
from orc.dal._decorators import requires_enabled
from orc.dal.sqlite import fetch_hubitat_devices, read_lights, write_light

_DB_TRUTH_DEVICE_TYPES = {"Generic Zigbee Outlet"}


def _hubitat_body_to_state(body):
    attrs = body["attributes"]
    return attrs["level"] if ("level" in attrs and attrs["switch"] == config.ON) else attrs["switch"]


@requires_enabled(lambda lights: m.Configs(*(m.Config(what=light, state=config.OFF) for light in lights)))
def fetch_light_states(lights):
    bodies = fetch_hubitat_devices()
    if bodies is None:
        return m.Configs(*(m.Config(what=light, state=config.OFF) for light in lights))
    stored = dict(read_lights())
    configs = []
    for light in lights:
        body = bodies[light.value]
        is_truth = body["type"] in _DB_TRUTH_DEVICE_TYPES
        if is_truth and light.value not in stored:
            write_light(light, type=body["type"], state=config.OFF)
            stored[light.value] = config.OFF
        state = stored[light.value] if is_truth else _hubitat_body_to_state(body)
        configs.append(m.Config(what=light, state=state))
    return m.Configs(*configs)


@requires_enabled(None)
def update_light(light, on=None, brightness=None):
    if brightness is not None:
        url = f"{config.base_url}/devices/{light.value}/setLevel/{brightness}{config.secrets.access_token}"
        new_state = brightness
    else:
        url = f"{config.base_url}/devices/{light.value}/{config.ON if on else config.OFF}{config.secrets.access_token}"
        new_state = config.ON if on else config.OFF
    body = requests.get(url, timeout=config.http_timeout).json()
    device_type = body.get("type", "")
    if device_type in _DB_TRUTH_DEVICE_TYPES:
        write_light(light, type=device_type, state=new_state)


@requires_enabled(None)
def reboot():
    requests.post(f"{config.base_url}/hub/reboot{config.secrets.access_token}", timeout=config.http_timeout)
