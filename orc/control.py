from enum import Enum
import time
import requests
from datetime import datetime, timedelta

from orc import config


def build_enums(config):
    json = requests.get(f"{config.BASE_URL}/devices{config.ACCESS_TOKEN}").json()

    enums = []
    for name, cfg in (
        ("Light", config.LIGHT_NAME_TO_HUBITAT),
        ("Sound", config.AUDIO_NAME_TO_HUBITAT),
    ):
        enums.append(
            Enum(name, {cfg[e["label"]]: e["id"] for e in json if e["label"] in cfg})
        )
    return tuple(enums)


Light, Sound = build_enums(config)


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


def set_sound(sound, lvl):
    def f():
        requests.get(
            f"{config.BASE_URL}/devices/{sound.value}/setVolume/{lvl}{config.ACCESS_TOKEN}"
        )

    return f


def build_schedule():
    today = datetime.now()

    sun_result = requests.get(f"{config.SUNRISE_URL}&date={today.date()}").json()[
        "results"
    ]
    reset = today.replace(hour=1)
    sunrise = datetime.fromisoformat(sun_result["sunrise"]) - timedelta(minutes=30)
    sunset = datetime.fromisoformat(sun_result["sunset"]) + timedelta(minutes=30)
    core_start = today.replace(hour=9, minute=0, second=0)
    core_end = today.replace(hour=22, minute=0, second=0)

    result = [
        (reset, set_light(e, on=False)) for e in Light if e != Light.BEDROOM_NIGHT_LIGHT
    ]
    result.append((sunrise, set_light(Light.LIVING_ROOM_FLOOR_LAMP, on=True)))
    result.append((sunrise, set_light(Light.KITCHEN_LIGHTS, on=True)))
    result.append((core_start, set_light(Light.KITCHEN_LIGHTS, on=False)))
    result.extend(((core_start, set_sound(e, 40)) for e in Sound))
    result.append((core_start, set_light(Light.LIVING_ROOM_DESK_LAMP, on=True)))
    result.append((core_start, set_light(Light.BEDROOM_NIGHT_LIGHT, on=False)))
    result.append((core_start, set_light(Light.ENTANCE_DESK_LAMP, brightness=100)))
    result.append((sunset, set_light(Light.BEDROOM_NIGHT_LIGHT, on=True)))
    result.extend(((core_end, set_sound(e, 10)) for e in Sound))
    return result
