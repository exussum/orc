import requests

from orc import config
from orc import model as m
from orc.dal import requires_enabled
from orc.dal.sqlite import _fetch_hubitat_device, _read_light, _write_light

_DB_TRUTH_DEVICE_TYPES = {"Generic Zigbee Outlet"}


@requires_enabled(lambda light: m.Config(what=light, state=config.OFF))
def fetch_light_state(light):
    device_type, stored = _read_light(light)
    body = _fetch_hubitat_device(light)

    if device_type in _DB_TRUTH_DEVICE_TYPES:
        return m.Config(what=light, state=stored or config.OFF)

    if body is None:
        return m.Config(what=light, state=config.OFF)

    if device_type is None:
        device_type = body.get("type", "")
        _write_light(light, type=device_type)
        if device_type in _DB_TRUTH_DEVICE_TYPES:
            return m.Config(what=light, state=stored or config.OFF)

    attrs = {e["name"]: e["currentValue"] for e in body["attributes"]}
    return m.Config(what=light, state=attrs["level"] if ("level" in attrs and attrs["switch"] == config.ON) else attrs["switch"])


@requires_enabled(None)
def update_light(light, on=None, brightness=None):
    if brightness is not None:
        requests.get(
            f"{config.base_url}/devices/{light.value}/setLevel/{brightness}{config.secrets.access_token}",
            timeout=config.http_timeout,
        )
        new_state = brightness
    else:
        requests.get(
            f"{config.base_url}/devices/{light.value}/{config.ON if on else config.OFF}{config.secrets.access_token}",
            timeout=config.http_timeout,
        )
        new_state = config.ON if on else config.OFF
    device_type, _ = _read_light(light)
    type_to_store = None
    if device_type is None:
        body = _fetch_hubitat_device(light)
        if body is not None:
            device_type = body.get("type", "")
            type_to_store = device_type

    state_to_store = new_state if device_type in _DB_TRUTH_DEVICE_TYPES else None
    if type_to_store is not None or state_to_store is not None:
        _write_light(light, type=type_to_store, state=state_to_store)
