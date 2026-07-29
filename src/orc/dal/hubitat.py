import threading

import requests

from orc import config
from orc import model as m
from orc.decorators import requires_enabled

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
def reboot() -> None:
    resp = _session().post(f"{config.hubitat_url}/hub/reboot{config.secrets.access_token}", timeout=config.http_timeout)
    resp.raise_for_status()
