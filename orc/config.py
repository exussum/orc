import os
from datetime import datetime, timedelta
from enum import Enum

from orc import dal, model
from orc.model import LightConfig, RoutineConfig, SoundConfig, Theme, scan

BASE_URL = os.getenv("BASE_URL")
ACCESS_TOKEN = "?access_token=" + os.getenv("ACCESS_TOKEN")
SUNRISE_URL = os.getenv("SUNRISE_URL")
MARKET_HOLIDAYS_URL = os.getenv("MARKET_HOLIDAYS_URL")

hubitat_config = dal.get_config()


def build_enum(name, hub_name_to_token, hubitat_config):
    id_lookup = {e["label"]: int(e["id"]) for e in hubitat_config}

    return Enum(
        name,
        {
            token: id_lookup.get(name, -(default + 1))
            for (default, (name, token)) in enumerate(hub_name_to_token.items())
        },
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

# fmt: off

CONFIGS = scan(
    Theme(
        days="non-holiday",
        name="workday",
        configs=(
            LightConfig(name="reset", when="1:00", what=set(Light) - {Light.BEDROOM_NIGHT_LIGHT}, state="off"),
            LightConfig(name="partner up", when="6:15", what=[Light.LIVING_ROOM_FLOOR_LAMP, Light.KITCHEN_LIGHTS], state="on"),
            RoutineConfig(name="partner leaving", when="7:00", items=(
                LightConfig(what=Light.ENTANCE_DESK_LAMP, state=1),
                LightConfig(what=[Light.BEDROOM_NIGHT_LIGHT, Light.KITCHEN_LIGHTS], state="off")
            )),
            RoutineConfig(name="up and atom", when="9:00", items=(
                SoundConfig(what=Sound, state=40),
                LightConfig(what=[Light.ENTANCE_DESK_LAMP, Light.OFFICE_DESK_LAMP], state=100),
                LightConfig(what=[Light.LIVING_ROOM_DESK_LAMP, Light.LIVING_ROOM_FLOOR_LAMP], state="on"),
                LightConfig(what=[Light.BEDROOM_NIGHT_LIGHT, Light.KITCHEN_LIGHTS], state="off"),
            )),
            LightConfig(name="reset office light", when="9:01", what=Light.OFFICE_DESK_LAMP, state="off"),
            LightConfig(name="sunset lights", when="sunset", what=[Light.BEDROOM_NIGHT_LIGHT, Light.KITCHEN_LIGHTS], state="on"),
            SoundConfig(name="quiet time", when="23:00", what=Sound, state=10),
        ),
    ),
    Theme(name="away", configs=()),
    Theme(
        name="babysitting",
        configs=(
            LightConfig(when="sunrise", what=Light.BEDROOM_NIGHT_LIGHT, state="off"),
            LightConfig(when="sunset", what=Light.BEDROOM_NIGHT_LIGHT, state="on"),
        ),
    ),
    Theme(
        days="holiday",
        name="day off",
        configs=(
            LightConfig(name="reset", when="2:00", what=set(Light) - {Light.BEDROOM_NIGHT_LIGHT}, state="off"),
            RoutineConfig(name="partner up", when="7:00", items=(
                LightConfig(what=[Light.LIVING_ROOM_FLOOR_LAMP, Light.KITCHEN_LIGHTS], state="on"),
                LightConfig(what=[Light.BEDROOM_NIGHT_LIGHT, Light.KITCHEN_LIGHTS], state="off"),
            )),
            RoutineConfig(name="up and atom", when="9:30", items=(
                SoundConfig(what=Sound, state=40),
                LightConfig(what=Light.ENTANCE_DESK_LAMP, state=100),
                LightConfig(what=[Light.LIVING_ROOM_DESK_LAMP, Light.LIVING_ROOM_FLOOR_LAMP], state="on"),
            )),
            LightConfig(name="reset office light", when="9:01", what=Light.OFFICE_DESK_LAMP, state="off"),
            LightConfig(name="sunset lights", when="sunset", what=[Light.BEDROOM_NIGHT_LIGHT, Light.KITCHEN_LIGHTS], state="on"),
            SoundConfig(name="quiet time", when="23:00", what=Sound, state=10),
        ),
    ),
)

# fmt:on
