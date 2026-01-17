import time

import requests

from orc import config
from orc import model as m


def get_light_state(light):
    resp = requests.get(f"{config.BASE_URL}/devices/{light.value}{config.ACCESS_TOKEN}")
    print(resp)
    if resp.status_code == 200:
        attrs = {e["name"]: e["currentValue"] for e in resp.json()["attributes"]}
        return m.Config(
            what=light, state=attrs["level"] if ("level" in attrs and attrs["switch"] == "on") else attrs["switch"]
        )
    else:
        return m.Config(what=light, state="off")


def set_light(light, on=None, brightness=None):
    print(light, on, brightness)
    return
    if brightness:
        requests.get(f"{config.BASE_URL}/devices/{light.value}/setLevel/{brightness}{config.ACCESS_TOKEN}")
    else:
        requests.get(f"{config.BASE_URL}/devices/{light.value}/{'on' if on else 'off'}{config.ACCESS_TOKEN}").content


def set_sound(sound, lvl):
    requests.get(f"{config.BASE_URL}/devices/{sound.value}/initialize{config.ACCESS_TOKEN}").json()
    time.sleep(0.1)
    requests.get(f"{config.BASE_URL}/devices/{sound.value}/setVolume/{lvl}{config.ACCESS_TOKEN}").json()


def get_config():
    return requests.get(f"{config.BASE_URL}/devices{config.ACCESS_TOKEN}").json()


def get_holidays():
    result = requests.get(f"{config.MARKET_HOLIDAYS_URL}").json()
    if "error" in result:
        print(result["error"])
        return []
    return result


def get_sun_cycle(date):
    return requests.get(f"{config.SUNRISE_URL}&date={date}").json()["results"]
