import time
from zoneinfo import ZoneInfo
import requests
from enum import Enum
from datetime import datetime, timedelta
from dataclasses import replace

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

        result.append((time, e))

    return result


def execute(rule):
    if isinstance(rule, config.RoutineConfig):
        for e in rule.items:
            execute(e)
    else:
        what = [rule.what] if isinstance(rule.what, Enum) else rule.what
        sleep = time.sleep if len(what) > 1 else (lambda _: 1)
        for w in what:
            if isinstance(rule, config.LightConfig):
                (
                    set_light(w, brightness=rule.state)
                    if isinstance(rule.state, int)
                    else set_light(w, on=rule.state == "on")
                )
            else:
                (cast_initialize(w) if rule.state == "initialize" else set_sound(w, rule.state))
            sleep(1)
