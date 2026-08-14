from typing import Any

from orc import model as m


class _AllSecret(dict[str, str]):
    def get(self, key: str, default: Any = None, /) -> str:
        return f"secret_{key}"


def fetch_secrets() -> m.Secrets:
    return m.Secrets(
        hubitat_access_token="secret_hubitat_access_token",
        market_holidays_url="secret_market_holidays_url",
        mqtt_user="secret_mqtt_user",
        mqtt_password="secret_mqtt_password",
        other=_AllSecret(),
    )
