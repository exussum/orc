import requests

from orc import config


def set_light(light, on=None, brightness=None):
    if brightness:
        requests.get(f"{config.BASE_URL}/devices/{light.value}/setLevel/{brightness}{config.ACCESS_TOKEN}")
    else:
        requests.get(f"{config.BASE_URL}/devices/{light.value}/{'on' if on else 'off'}{config.ACCESS_TOKEN}").content


def cast_initialize(sound):
    requests.get(f"{config.BASE_URL}/devices/{sound.value}/initialize{config.ACCESS_TOKEN}").json()


def set_sound(sound, lvl):
    requests.get(f"{config.BASE_URL}/devices/{sound.value}/setVolume/{lvl}{config.ACCESS_TOKEN}").json()


def get_config():
    return requests.get(f"{config.BASE_URL}/devices{config.ACCESS_TOKEN}").json()


def get_holidays():
    return requests.get(f"{config.MARKET_HOLIDAYS_URL}").json()


def get_sun_cycle(date):
    return requests.get(f"{config.SUNRISE_URL}&date={date}").json()["results"]
