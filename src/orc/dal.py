import os
import time
from functools import lru_cache
from urllib.request import urlopen

import icalendar
import recurring_ical_events
import requests
from bitwarden_sdk import BitwardenClient, DeviceType, client_settings_from_dict

from orc import config
from orc import model as m


def _get_url_value(url):
    with urlopen(url) as response:
        return response.readline().decode("utf-8").strip()


def get_secrets():
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
        access_token="?access_token=" + get_secret("ACCESS_TOKEN"),
        market_holidays_url=get_secret("MARKET_HOLIDAYS_URL"),
        ics_url=get_secret("ICS_URL"),
    )


def get_light_state(light):
    resp = requests.get(f"{config.BASE_URL}/devices/{light.value}{config.SECRETS.access_token}", timeout=config.HTTP_TIMEOUT)
    if resp.status_code == 200:
        attrs = {e["name"]: e["currentValue"] for e in resp.json()["attributes"]}
        return m.Config(what=light, state=attrs["level"] if ("level" in attrs and attrs["switch"] == "on") else attrs["switch"])
    else:
        return m.Config(what=light, state="off")


def set_light(light, on=None, brightness=None):
    if brightness:
        requests.get(
            f"{config.BASE_URL}/devices/{light.value}/setLevel/{brightness}{config.SECRETS.access_token}",
            timeout=config.HTTP_TIMEOUT,
        )
    else:
        requests.get(
            f"{config.BASE_URL}/devices/{light.value}/{'on' if on else 'off'}{config.SECRETS.access_token}",
            timeout=config.HTTP_TIMEOUT,
        ).content


def set_sound(sound, lvl):
    requests.get(f"{config.BASE_URL}/devices/{sound.value}/speak/%20{config.SECRETS.access_token}", timeout=config.HTTP_TIMEOUT).json()
    time.sleep(1)
    requests.get(
        f"{config.BASE_URL}/devices/{sound.value}/setVolume/{lvl}{config.SECRETS.access_token}",
        timeout=config.HTTP_TIMEOUT,
    ).json()


def get_config():
    return requests.get(f"{config.BASE_URL}/devices{config.SECRETS.access_token}", timeout=config.HTTP_TIMEOUT).json()


@lru_cache(maxsize=2)
def get_holidays(year):
    result = requests.get(config.SECRETS.market_holidays_url, timeout=config.HTTP_TIMEOUT).json()
    if "error" in result:
        print(result["error"])
        return []
    return result


def read_ical(start, end):
    ical_string = requests.get(config.SECRETS.ics_url, timeout=config.HTTP_ICAL_TIMEOUT).content
    a_calendar = icalendar.Calendar.from_ical(ical_string)
    # between leaks out events that have already started
    return (e for e in recurring_ical_events.of(a_calendar).between(start, end) if e.start >= start)
