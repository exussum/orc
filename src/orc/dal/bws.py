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
    ids = c.secrets().list(_get_url_value(os.environ["BWS_ORG_ID"])).data
    secrets = {s.key: s.value for s in c.secrets().get_by_ids([e.id for e in ids.data]).data.data}
    return m.Secrets(
        access_token="?access_token=" + secrets["HUBITAT_ACCESS_TOKEN"],
        market_holidays_url=secrets["MARKET_HOLIDAYS_URL"],
        ics_url=secrets["ICS_URL"],
        yolink_id=secrets["YOLINK_ID"],
        yolink_secret=secrets["YOLINK_SECRET"],
    )


def _get_url_value(url):
    with urlopen(url) as response:  # nosemgrep: dynamic-urllib-use-detected
        return response.readline().decode("utf-8").strip()
