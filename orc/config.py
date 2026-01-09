import os
from datetime import datetime, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

import orc.model as m
from orc import dal

BASE_URL = os.getenv("BASE_URL", "http://base.example.com")
ACCESS_TOKEN = "?access_token=" + os.getenv("ACCESS_TOKEN", "example-token")
SUNRISE_URL = os.getenv("SUNRISE_URL", "http://sunrise.example.com")
MARKET_HOLIDAYS_URL = os.getenv("MARKET_HOLIDAYS_URL", "http://holidays.exmaple.com")
ENABLED = os.getenv("ENABLED", "")

hubitat_config = dal.get_config() if ENABLED else {}

TZ = ZoneInfo("America/New_York")


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
        "entrance bulb 1": "ENTRANCE_BULB_1",
        "entrance bulb 2": "ENTRANCE_BULB_2",
        "kitchen lights": "KITCHEN_LIGHTS",
        "living room desk lamp": "LIVING_ROOM_DESK_LAMP",
        "living room floor lamp": "LIVING_ROOM_FLOOR_LAMP",
        "office desk lamp": "OFFICE_DESK_LAMP",
        "office table lamp": "OFFICE_TABLE_LAMP",
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

CONFIGS = m.scan(
    m.Theme(
        name="work day",
        configs=(
            m.LightConfig(name="reset", when="1:00", what=set(Light) - {Light.BEDROOM_NIGHT_LIGHT}, state="off", mandatory=True),
            m.LightConfig(name="partner up", when="6:15", what=[Light.LIVING_ROOM_FLOOR_LAMP, Light.KITCHEN_LIGHTS], state="on"),
            m.RoutineConfig(name="partner leaving", when="7:00", items=(
                m.LightSubConfig(what=Light.ENTANCE_DESK_LAMP, state=1),
                m.LightSubConfig(what=Light.KITCHEN_LIGHTS, state="off"),
            )),
            m.RoutineConfig(name="up and atom", when="9:00", items=(
                m.LightSubConfig(what=[Light.ENTANCE_DESK_LAMP, Light.OFFICE_TABLE_LAMP], state=100),
                m.LightSubConfig(what=[Light.LIVING_ROOM_DESK_LAMP, Light.LIVING_ROOM_FLOOR_LAMP], state="on"),
                m.LightSubConfig(what=[Light.BEDROOM_NIGHT_LIGHT, Light.KITCHEN_LIGHTS], state="off"),
                m.SoundSubConfig(what=Sound, state=40),
                m.LightSubConfig(what=[Light.OFFICE_TABLE_LAMP, Light.BEDROOM_NIGHT_LIGHT], state="off"),
            )),
            m.LightConfig(name="sunset lights", when="sunset", what=[Light.BEDROOM_NIGHT_LIGHT, Light.KITCHEN_LIGHTS], state="on", mandatory=True),
            m.SoundConfig(name="quiet time", when="23:00", what=Sound, state=10, mandatory=True),
        ),
    ),
    m.Theme(name="away", configs=(m.LightConfig(name="reset", when="1:00", what=set(Light), mandatory=True, state="off"),)),
    m.Theme(
        name="babysitter",
        configs=(
            m.LightConfig(name="nightlight off", when="sunrise", what=Light.BEDROOM_NIGHT_LIGHT, state="off"),
            m.LightConfig(name="nightlight on", when="sunset", what=Light.BEDROOM_NIGHT_LIGHT, state="on"),
        ),
    ),
    m.Theme(
        name="home alone",
        configs=(
            m.LightConfig(name="reset", when="1:00", what=set(Light) - {Light.BEDROOM_NIGHT_LIGHT}, state="off", mandatory=True),
            m.RoutineConfig(name="up and atom", when="9:30", items=(
                m.LightSubConfig(what=[Light.ENTANCE_DESK_LAMP, Light.OFFICE_TABLE_LAMP], state=100),
                m.LightSubConfig(what=[Light.LIVING_ROOM_DESK_LAMP, Light.LIVING_ROOM_FLOOR_LAMP], state="on"),
                m.SoundSubConfig(what=Sound, state=40),
                m.LightSubConfig(what=[Light.OFFICE_TABLE_LAMP, Light.BEDROOM_NIGHT_LIGHT], state="off"),
            )),
            m.LightConfig(name="sunset lights", when="sunset", what=[Light.BEDROOM_NIGHT_LIGHT, Light.KITCHEN_LIGHTS], state="on", mandatory=True),
            m.SoundConfig(name="quiet time", when="23:00", what=Sound, state=10, mandatory=True),
        ),
    ),
    m.Theme(
        name="day off",
        configs=(
            m.LightConfig(name="reset", when="1:00", what=set(Light) - {Light.BEDROOM_NIGHT_LIGHT}, state="off", mandatory=True),
            m.LightConfig(name="partner up", when="7:00", what=[Light.LIVING_ROOM_FLOOR_LAMP, Light.KITCHEN_LIGHTS], state="on"),
            m.RoutineConfig(name="up and atom", when="9:30", items=(
                m.LightSubConfig(what=[Light.ENTANCE_DESK_LAMP, Light.OFFICE_TABLE_LAMP], state=100),
                m.LightSubConfig(what=[Light.LIVING_ROOM_DESK_LAMP, Light.LIVING_ROOM_FLOOR_LAMP], state="on"),
                m.SoundSubConfig(what=Sound, state=40),
                m.LightSubConfig(what=[Light.BEDROOM_NIGHT_LIGHT, Light.OFFICE_TABLE_LAMP], state="off"),
            )),
            m.LightConfig(name="sunset lights", when="sunset", what=[Light.BEDROOM_NIGHT_LIGHT, Light.KITCHEN_LIGHTS], state="on", mandatory=True),
            m.SoundConfig(name="quiet time", when="23:00", what=Sound, state=10, mandatory=True),
        ),
    ),
)

BUTTON_CONFIGS = {
    "Partial TV Lights": m.AdHocRoutineConfig(
        items=(
            m.LightSubConfig(
                what=set(Light) - {Light.LIVING_ROOM_FLOOR_LAMP, Light.ENTANCE_DESK_LAMP, Light.OFFICE_TABLE_LAMP, Light.BEDROOM_NIGHT_LIGHT},
                state="off",
            ),
            m.LightSubConfig(what=Light.LIVING_ROOM_FLOOR_LAMP, state="on"),
            m.LightSubConfig(what=[Light.ENTANCE_DESK_LAMP, Light.OFFICE_TABLE_LAMP], state=1),
        )
    ),
    "TV Lights": m.AdHocRoutineConfig(
        items=(
            m.LightSubConfig(
                what=set(Light) - {Light.ENTANCE_DESK_LAMP, Light.OFFICE_TABLE_LAMP, Light.KITCHEN_LIGHTS, Light.BEDROOM_NIGHT_LIGHT},
                state="off",
            ),
            m.LightSubConfig(what=[Light.ENTANCE_DESK_LAMP, Light.OFFICE_TABLE_LAMP], state=1),
            m.LightSubConfig(what=Light.KITCHEN_LIGHTS, state="on"),
        )
    ),
    "Front Rooms": m.AdHocRoutineConfig(
        items=(
            m.LightSubConfig(what=[Light.LIVING_ROOM_DESK_LAMP, Light.LIVING_ROOM_FLOOR_LAMP], state="on"),
            m.LightSubConfig(what=Light.ENTANCE_DESK_LAMP, state=100),
            m.LightSubConfig(
                what=[Light.OFFICE_DESK_LAMP, Light.OFFICE_TABLE_LAMP, Light.OFFICE_FLOOR_LAMP, Light.KITCHEN_LIGHTS],
                state="off",
            ),
        )
    ),
    "Early Morning Lights": m.LightSubConfig(what=[Light.LIVING_ROOM_FLOOR_LAMP, Light.KITCHEN_LIGHTS], state="on"),
    "All Lights On": m.AdHocRoutineConfig(
        items=(m.LightSubConfig(what=Light, state="on"), m.LightSubConfig(what=Light, state=100))
    ),
    "All Lights Off": m.AdHocRoutineConfig(items=(m.LightSubConfig(what=Light, state="off"),)),
    "Test": m.AdHocRoutineConfig(
        items=(
            m.LightSubConfig(what=Light, state="off"),
            m.LightSubConfig(what=Light, state="on"),
        )
    ),
}

# fmt: on
