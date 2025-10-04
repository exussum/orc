import time
from zoneinfo import ZoneInfo
import requests
from enum import Enum
from datetime import datetime, timedelta
from dataclasses import replace

from orc import config


def set_light(light, on=None, brightness=None):
    def f():
        if brightness:
            requests.get(f"{config.BASE_URL}/devices/{light.value}/setLevel/{brightness}{config.ACCESS_TOKEN}")
        else:
            requests.get(
                f"{config.BASE_URL}/devices/{light.value}/{'on' if on else 'off'}{config.ACCESS_TOKEN}"
            ).content

    return f


def cast_initialize(sound):
    def f():
        requests.get(f"{config.BASE_URL}/devices/{sound.value}/initialize{config.ACCESS_TOKEN}").json()

    return f


def set_sound(sound, lvl):
    def f():
        requests.get(f"{config.BASE_URL}/devices/{sound.value}/setVolume/{lvl}{config.ACCESS_TOKEN}").json()

    return f


def build_schedule():
    now = datetime.now(tz=ZoneInfo("America/New_York"))
    sun_result = requests.get(f"{config.SUNRISE_URL}&date={now.date()}").json()["results"]
    sunrise = datetime.fromisoformat(sun_result["sunrise"])
    sunset = datetime.fromisoformat(sun_result["sunset"])

    result = []
    for e in config.CONFIGS:
        if e.when == "sunrise":
            time = sunrise
        elif e.when == "sunset":
            time = sunset
        else:
            time = now.replace(hour=e.when.hour, minute=e.when.minute, second=0)
        time = time + e.offset

        what = [e.what] if isinstance(e.what, Enum) else e.what

        for w in what:
            if isinstance(e, config.LightConfig):
                f = set_light(w, brightness=e.state) if isinstance(e.state, int) else set_light(w, on=e.state == "on")
            else:
                f = cast_initialize(w) if e.state == "initialize" else set_sound(w, e.state)

            debug_config = replace(e)
            debug_config.what = w
            result.append((time, f, debug_config))

    return result
