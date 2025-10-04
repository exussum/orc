from datetime import timedelta, datetime
from enum import Enum
import itertools
import os
import requests

from orc.model import LightConfig, SoundConfig
from orc import model

BASE_URL = os.getenv("BASE_URL")
ACCESS_TOKEN = "?access_token=" + os.getenv("ACCESS_TOKEN")
SUNRISE_URL = os.getenv("SUNRISE_URL")

hubitat_config = requests.get(f"{BASE_URL}/devices{ACCESS_TOKEN}").json()


def build_enum(name, hub_name_to_token, hubitat_config):
    return Enum(
        name,
        {hub_name_to_token[e["label"]]: e["id"] for e in hubitat_config if e["label"] in hub_name_to_token},
    )


Light = build_enum(
    "Light",
    {
        "night light": "BEDROOM_NIGHT_LIGHT",
        "entrance desk lamp": "ENTANCE_DESK_LAMP",
        "kitchen lights": "KITCHEN_LIGHTS",
        "living room desk lamp": "LIVING_ROOM_DESK_LAMP",
        "living room floor lamp": "LIVING_ROOM_FLOOR_LAMP",
        "office desk lamp": "OFFICE_DESK_LAMP",
        "office floor lamp": "OFFICE_FLOOR_LAMP",
    },
    hubitat_config,
)
Sound = build_enum(
    "Sound",
    {
        "Living room mini": "LIVING_ROOM_MINI",
        "Bedroom display": "BEDROOM_DISPLAY",
        "Office display": "OFFICE_DISPLAY",
        "Kitchen mini": "KITCHEN_MINI",
    },
    hubitat_config,
)


CONFIGS = (
    LightConfig(when="1:00", what=set(Light) - {Light.BEDROOM_NIGHT_LIGHT}, state="off"),
    LightConfig(
        when="sunrise", what=[Light.LIVING_ROOM_FLOOR_LAMP, Light.KITCHEN_LIGHTS], state="on", offset="-60 minutes"
    ),
    LightConfig(when="sunrise", what=Light.ENTANCE_DESK_LAMP, state=1),
    SoundConfig(when="8:59", what=Sound, state="initialize"),
    SoundConfig(when="9:00", what=Sound, state=40),
    LightConfig(when="9:00", what=Light.ENTANCE_DESK_LAMP, state=100),
    LightConfig(when="9:00", what=[Light.LIVING_ROOM_DESK_LAMP, Light.LIVING_ROOM_FLOOR_LAMP], state="on"),
    LightConfig(when="9:00", what=Light.BEDROOM_NIGHT_LIGHT, state="off"),
    LightConfig(when="sunset", what=Light.BEDROOM_NIGHT_LIGHT, state="on"),
    SoundConfig(when="22:59", what=Sound, state="initialize"),
    SoundConfig(when="23:00", what=Sound, state=10),
)
