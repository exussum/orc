import icmplib
import requests

from orc import config
from orc.dal import requires_enabled


@requires_enabled({})
def fetch_hubitat_config(secrets):
    result = requests.get(f"{config.base_url}/devices{secrets.access_token}", timeout=config.http_timeout).json()
    return {e["label"]: int(e["id"]) for e in result}


@requires_enabled(False)
def ping_host(hostname):
    return icmplib.ping(hostname, count=2, timeout=1, privileged=True).is_alive
