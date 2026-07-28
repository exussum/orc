from collections.abc import Iterable
from typing import Any

import requests

from orc import config
from orc import model as m
from orc.dal.decorators import requires_enabled
from orc.dal.sqlite import read_lights, write_light

_DB_TRUTH_DEVICE_TYPES: set[str] = {"Generic Zigbee Outlet"}

_CAPABILITY_MAP: dict[str, m.Capability] = {
    "ChangeLevel": m.Capability.change_level,
}


@requires_enabled({})
def fetch_hubitat_config(secrets: m.Secrets) -> dict[str, tuple[int, frozenset[m.Capability]]]:
    resp = requests.get(f"{config.hubitat_url}/devices/all{secrets.access_token}", timeout=config.http_timeout)
    resp.raise_for_status()
    return {
        e["label"]: (int(e["id"]), frozenset(_CAPABILITY_MAP[c] for c in e.get("capabilities", []) if c in _CAPABILITY_MAP))
        for e in resp.json()
    }


@requires_enabled(lambda lights: m.Configs(*(m.Config(what=light, state=m.OFF) for light in lights)))
def fetch_light_states(lights: Iterable[m.DeviceEnum]) -> m.Configs:
    bodies = _fetch_hubitat_devices()
    stored = dict(read_lights())
    configs: list[m.Config] = []
    for light in lights:
        body = bodies.get(light.value)
        if body is None:
            # Virtual devices (negative synthetic id) and devices removed upstream aren't in
            # Hubitat's device list; dispatch skips them, so report them as off rather than KeyError.
            configs.append(m.Config(what=light, state=m.OFF))
            continue
        is_truth = body["type"] in _DB_TRUTH_DEVICE_TYPES
        if is_truth and light.value not in stored:
            write_light(light, type=body["type"], state=m.OFF)
            stored[light.value] = m.OFF
        state = stored[light.value] if is_truth else _hubitat_body_to_state(body)
        configs.append(m.Config(what=light, state=state))
    return m.Configs(*configs)


@requires_enabled(None)
def update_light(light: m.DeviceEnum, on: bool | None = None, brightness: int | None = None) -> None:
    if brightness is not None and m.Capability.change_level in light.capabilities:
        url = f"{config.hubitat_url}/devices/{light.value}/setLevel/{brightness}{config.secrets.access_token}"
        new_state: int | str = brightness
    else:
        if brightness == 0:
            on = False
        elif brightness == 100:
            on = True
        elif brightness is not None:
            raise ValueError(f"{light.name} does not support ChangeLevel; cannot set brightness {brightness}")
        url = f"{config.hubitat_url}/devices/{light.value}/{m.ON if on else m.OFF}{config.secrets.access_token}"
        new_state = m.ON if on else m.OFF
    resp = requests.get(url, timeout=config.http_timeout)
    resp.raise_for_status()
    device_type = resp.json().get("type", "")
    if device_type in _DB_TRUTH_DEVICE_TYPES:
        write_light(light, type=device_type, state=new_state)


@requires_enabled(None)
def reboot() -> None:
    resp = requests.post(f"{config.hubitat_url}/hub/reboot{config.secrets.access_token}", timeout=config.http_timeout)
    resp.raise_for_status()


def _fetch_hubitat_devices() -> dict[int, Any]:
    resp = requests.get(f"{config.hubitat_url}/devices/all{config.secrets.access_token}", timeout=config.http_timeout)
    resp.raise_for_status()
    return {int(d["id"]): d for d in resp.json()}


def _hubitat_body_to_state(body: Any) -> int | str:
    attrs = body["attributes"]
    switch = attrs.get("switch", m.OFF)
    return int(attrs["level"]) if ("level" in attrs and switch == m.ON) else switch
