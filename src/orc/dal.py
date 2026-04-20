import time
from functools import lru_cache

import icalendar
import recurring_ical_events
import requests

from orc import config
from orc import model as m


_TIMEOUT = 5


def get_light_state(light):
    resp = requests.get(f"{config.BASE_URL}/devices/{light.value}{config.ACCESS_TOKEN}", timeout=_TIMEOUT)
    if resp.status_code == 200:
        attrs = {e["name"]: e["currentValue"] for e in resp.json()["attributes"]}
        return m.Config(what=light, state=attrs["level"] if ("level" in attrs and attrs["switch"] == "on") else attrs["switch"])
    else:
        return m.Config(what=light, state=config.OFF)


def set_light(light, on=None, brightness=None):
    if brightness:
        requests.get(f"{config.BASE_URL}/devices/{light.value}/setLevel/{brightness}{config.ACCESS_TOKEN}", timeout=_TIMEOUT)
    else:
        requests.get(f"{config.BASE_URL}/devices/{light.value}/{'on' if on else 'off'}{config.ACCESS_TOKEN}", timeout=_TIMEOUT).content


def set_sound(sound, lvl):
    requests.get(f"{config.BASE_URL}/devices/{sound.value}/speak/%20{config.ACCESS_TOKEN}", timeout=_TIMEOUT).json()
    time.sleep(1)
    requests.get(f"{config.BASE_URL}/devices/{sound.value}/setVolume/{lvl}{config.ACCESS_TOKEN}", timeout=_TIMEOUT).json()


def get_config():
    return requests.get(f"{config.BASE_URL}/devices{config.ACCESS_TOKEN}", timeout=_TIMEOUT).json()


@lru_cache(maxsize=2)
def get_holidays(year):
    result = requests.get(f"{config.MARKET_HOLIDAYS_URL}", timeout=_TIMEOUT).json()
    if "error" in result:
        print(result["error"])
        return []
    return result


def read_ical(start, end):
    ical_string = requests.get(config.ICS_URL, timeout=120).content
    a_calendar = icalendar.Calendar.from_ical(ical_string)
    # between leaks out events that have already started
    return (e for e in recurring_ical_events.of(a_calendar).between(start, end) if e.start >= start)
