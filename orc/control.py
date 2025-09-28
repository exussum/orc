from enum import Enum
import time
import requests
from datetime import datetime

from orc import config


def build_enum():
    result = requests.get(f"{config.BASE_URL}/devices{config.ACCESS_TOKEN}")
    return Enum(
        "Light", {config.NAME_TO_HUBITAT[e["label"]]: e["id"] for e in result.json()}
    )


Lights = build_enum()


def set_light(light, on=None, brightness=None):
    def f():
        if brightness:
            requests.get(
                f"{config.BASE_URL}/devices/{light.value}/setLevel/{brightness}{config.ACCESS_TOKEN}"
            )
        else:
            requests.get(
                f"{config.BASE_URL}/devices/{light.value}/{'on' if on else 'off'}{config.ACCESS_TOKEN}"
            ).content

    return f


def build_schedule():
    today = datetime.now()

    sun_result = requests.get(f"{config.SUNRISE_URL}&date={today.date()}").json()[
        "results"
    ]
    reset = today.replace(hour=1)
    sunrise = datetime.fromisoformat(sun_result["sunrise"]).timedelta(hours=-1)
    core_start = today.replace(hour=9, minute=15, second=0)
    core_end = today.replace(hour=22, minute=30, second=0)

    result = [
        (reset, set_light(e, on=False))
        for e in Lights
        if e != Lights.BEDROOM_NIGHT_LIGHT
    ]
    result.append((sunrise, set_light(Lights.LIVING_ROOM_DESK_LAMP, on=True)))
    result.append((sunrise, set_light(Lights.LIVING_ROOM_FLOOR_LAMP, on=True)))
    result.append((core_start, set_light(Lights.BEDROOM_NIGHT_LIGHT, on=False)))
    result.append((core_start, set_light(Lights.ENTANCE_DESK_LAMP, brightness=100)))
    result.append((core_end, set_light(Lights.BEDROOM_NIGHT_LIGHT, on=True)))
    return result
