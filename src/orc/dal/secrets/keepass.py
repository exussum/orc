from collections.abc import Callable
from getpass import getpass
from pathlib import Path
from typing import Any

from command_cfg import ConfigError
from pykeepass import PyKeePass
from pykeepass.exceptions import CredentialsError

import orc
from orc import model as m

REQUIRED_SECRETS: dict[str, Callable[[str], Any]] = {}


def fetch_secrets() -> m.Secrets:
    path = Path(orc.config.config_dir) / "secrets.kdbx"
    try:
        entries = PyKeePass(path, password=getpass("Secrets password: ")).entries
    except CredentialsError:
        raise ConfigError("secrets: the password does not open the database") from None
    secrets = {entry.title: entry.password or "" for entry in entries}
    return m.Secrets(
        market_holidays_url=secrets.pop("MARKET_HOLIDAYS_URL", ""),
        mqtt_user=secrets.pop("MQTT_USER", ""),
        mqtt_password=secrets.pop("MQTT_PASSWORD", ""),
        vapid_private_key=secrets.pop("VAPID_PRIVATE_KEY", ""),
        other=secrets,
    )
