import requests

from orc import config
from orc import model as m
from orc.dal._decorators import requires_enabled
from orc.dal.sqlite import read_lights, write_light

_DB_TRUTH_DEVICE_TYPES = {"Generic Zigbee Outlet"}


@requires_enabled({})
def fetch_hubitat_config(secrets):
    resp = requests.get(f"{config.base_url}/devices/all{secrets.access_token}", timeout=config.http_timeout)
    resp.raise_for_status()
    return {e["label"]: (int(e["id"]), frozenset(e.get("capabilities", []))) for e in resp.json()}


@requires_enabled(lambda lights: m.Configs(*(m.Config(what=light, state=config.OFF) for light in lights)))
def fetch_light_states(lights):
    bodies = _fetch_hubitat_devices()
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
    if brightness is not None and "ChangeLevel" in light.capabilities:
        url = f"{config.base_url}/devices/{light.value}/setLevel/{brightness}{config.secrets.access_token}"
        new_state = brightness
    else:
        if brightness == 0:
            on = False
        elif brightness == 100:
            on = True
        elif brightness is not None:
            raise ValueError(f"{light.name} does not support ChangeLevel; cannot set brightness {brightness}")
        url = f"{config.base_url}/devices/{light.value}/{config.ON if on else config.OFF}{config.secrets.access_token}"
        new_state = config.ON if on else config.OFF
    resp = requests.get(url, timeout=config.http_timeout)
    resp.raise_for_status()
    device_type = resp.json().get("type", "")
    if device_type in _DB_TRUTH_DEVICE_TYPES:
        write_light(light, type=device_type, state=new_state)


@requires_enabled(None)
def reboot():
    resp = requests.post(f"{config.base_url}/hub/reboot{config.secrets.access_token}", timeout=config.http_timeout)
    resp.raise_for_status()


def _fetch_hubitat_devices():
    resp = requests.get(f"{config.base_url}/devices/all{config.secrets.access_token}", timeout=config.http_timeout)
    resp.raise_for_status()
    return {int(d["id"]): d for d in resp.json()}


def _hubitat_body_to_state(body):
    attrs = body["attributes"]
    return int(attrs["level"]) if ("level" in attrs and attrs["switch"] == config.ON) else attrs["switch"]
