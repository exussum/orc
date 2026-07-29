import threading

import requests

from orc import config
from orc import model as m
from orc.dal.decorators import requires_enabled

_CAPABILITY_MAP: dict[str, m.Capability] = {
    "ChangeLevel": m.Capability.change_level,
}

# requests.Session isn't thread-safe; scheduler jobs run on multiple threads
_local = threading.local()


def _session() -> requests.Session:
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
    return _local.session


@requires_enabled({})
def fetch_hubitat_config(secrets: m.Secrets) -> dict[str, tuple[int, frozenset[m.Capability]]]:
    resp = _session().get(f"{config.hubitat_url}/devices/all{secrets.access_token}", timeout=config.http_timeout)
    resp.raise_for_status()
    return {
        e["label"]: (int(e["id"]), frozenset(_CAPABILITY_MAP[c] for c in e.get("capabilities", []) if c in _CAPABILITY_MAP))
        for e in resp.json()
    }


@requires_enabled(None)
def update_light(light: m.DeviceEnum, on: bool | None = None, brightness: int | None = None) -> None:
    if brightness is not None and m.Capability.change_level in light.capabilities:
        url = f"{config.hubitat_url}/devices/{light.value}/setLevel/{brightness}{config.secrets.access_token}"
    else:
        if brightness == 0:
            on = False
        elif brightness == 100:
            on = True
        elif brightness is not None:
            raise ValueError(f"{light.name} does not support ChangeLevel; cannot set brightness {brightness}")
        url = f"{config.hubitat_url}/devices/{light.value}/{m.ON if on else m.OFF}{config.secrets.access_token}"
    _session().get(url, timeout=config.http_timeout).raise_for_status()


@requires_enabled(None)
def reboot() -> None:
    resp = _session().post(f"{config.hubitat_url}/hub/reboot{config.secrets.access_token}", timeout=config.http_timeout)
    resp.raise_for_status()
