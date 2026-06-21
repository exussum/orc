import os
from urllib.request import urlopen

from bitwarden_sdk import BitwardenClient, DeviceType, client_settings_from_dict

from orc import model as m


def fetch_secrets():
    c = BitwardenClient(
        client_settings_from_dict(
            {
                "apiUrl": "https://vault.bitwarden.com/api",
                "identityUrl": "https://vault.bitwarden.com/identity",
                "userAgent": "orc",
                "deviceType": DeviceType.SDK,
            }
        )
    )
    c.auth().login_access_token(_get_url_value(os.environ["BWS_ACCESS_TOKEN"]))
    secrets = c.secrets().list(_get_url_value(os.environ["BWS_ORG_ID"])).data

    def get_secret(secret_name):
        return next(c.secrets().get(e.id).data.value for e in secrets.data if e.key == secret_name)

    return m.Secrets(
        access_token="?access_token=" + get_secret("HUBITAT_ACCESS_TOKEN"),
        market_holidays_url=get_secret("MARKET_HOLIDAYS_URL"),
        ics_url=get_secret("ICS_URL"),
        yolink_id=get_secret("YOLINK_ID"),
        yolink_secret=get_secret("YOLINK_SECRET"),
    )


def _get_url_value(url):
    with urlopen(url) as response:
        return response.readline().decode("utf-8").strip()
