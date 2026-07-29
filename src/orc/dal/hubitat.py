"""The last Maker API call: everything else (state, events, commands, discovery)
moved to the hub's MQTT export in orc.dal.mqtt."""

import requests

from orc import config
from orc.decorators import requires_enabled


@requires_enabled(None)
def reboot() -> None:
    resp = requests.post(f"{config.hubitat_url}/hub/reboot{config.secrets.access_token}", timeout=config.http_timeout)
    resp.raise_for_status()
