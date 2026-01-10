import os
from datetime import datetime, timedelta
from dataclasses import replace
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
        "night light": "BEDROOM_NIGHTLIGHT",
        "entrance desk lamp": "ENTANCE_DESK",
        "entrance bulb 1": "ENTRANCE_BULB_1",
        "entrance bulb 2": "ENTRANCE_BULB_2",
        "kitchen lights": "KITCHEN",
        "living room desk lamp": "g_LAMP",
        "living room floor lamp": "LIVING_ROOM_FLOOR",
        "office desk lamp": "OFFICE_DESK",
        "office table lamp": "OFFICE_TABLE",
        "office floor lamp": "OFFICE_FLOOR",
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


CONFIG_RESET_LIGHT = m.LightConfig(
    name="reset", when="1:00", what=set(Light) - {Light.BEDROOM_NIGHTLIGHT}, state="off", mandatory=True
)

CONFIG_PARTNER_UP = m.LightConfig(
    name="partner up", when="6:15", what=[Light.LIVING_ROOM_FLOOR, Light.KITCHEN], state="on"
)

CONFIG_UP_AND_ATOM = m.RoutineConfig(
    name="up and atom",
    when="9:00",
    items=(
        m.LightSubConfig(what=[Light.ENTANCE_DESK, Light.OFFICE_TABLE], state=100),
        m.LightSubConfig(what=[Light.g_LAMP, Light.LIVING_ROOM_FLOOR], state="on"),
        m.LightSubConfig(what=[Light.BEDROOM_NIGHTLIGHT, Light.KITCHEN], state="off"),
        m.SoundSubConfig(what=Sound, state=40),
        m.LightSubConfig(what=[Light.OFFICE_TABLE, Light.BEDROOM_NIGHTLIGHT], state="off"),
    ),
)

CONFIG_SUNSET_LIGHTS = m.LightConfig(
    name="sunset lights",
    when="sunset",
    what=[Light.BEDROOM_NIGHTLIGHT, Light.KITCHEN],
    state="on",
    mandatory=True,
)

CONFIG_QUIET_TIME = m.SoundConfig(name="quiet time", when="23:00", what=Sound, state=10, mandatory=True)

CONFIG_PARTNER_LEAVING = m.RoutineConfig(
    name="partner leaving",
    when="7:00",
    items=(
        m.LightSubConfig(what=Light.ENTANCE_DESK, state=1),
        m.LightSubConfig(what=Light.KITCHEN, state="off"),
    ),
)

CONFIGS = m.scan(
    m.Theme(
        name="work day",
        configs=(
            CONFIG_RESET_LIGHT,
            replace(CONFIG_PARTNER_UP, when="6:15"),
            CONFIG_PARTNER_LEAVING,
            replace(CONFIG_UP_AND_ATOM, when="9:00"),
            CONFIG_SUNSET_LIGHTS,
            CONFIG_QUIET_TIME,
        ),
    ),
    m.Theme(name="away", configs=(CONFIG_RESET_LIGHT,)),
    m.Theme(
        name="babysitter",
        configs=(
            m.LightConfig(name="nightlight off", when="sunrise", what=Light.BEDROOM_NIGHTLIGHT, state="off"),
            m.LightConfig(name="nightlight on", when="sunset", what=Light.BEDROOM_NIGHTLIGHT, state="on"),
        ),
    ),
    m.Theme(
        name="home alone",
        configs=(
            CONFIG_RESET_LIGHT,
            replace(CONFIG_UP_AND_ATOM, when="9:30"),
            CONFIG_SUNSET_LIGHTS,
            CONFIG_QUIET_TIME,
        ),
    ),
    m.Theme(
        name="day off",
        configs=(
            CONFIG_RESET_LIGHT,
            replace(CONFIG_PARTNER_UP, when="7:00"),
            replace(CONFIG_UP_AND_ATOM, when="9:30"),
            CONFIG_SUNSET_LIGHTS,
            CONFIG_QUIET_TIME,
        ),
    ),
)

BUTTON_CONFIGS = {
    "Partial TV Lights": m.AdHocRoutineConfig(
        items=(
            m.LightSubConfig(
                what=set(Light) - {Light.ENTANCE_DESK, Light.OFFICE_TABLE, Light.KITCHEN, Light.LIVING_ROOM_FLOOR, Light.BEDROOM_NIGHTLIGHT},
                state="off",
            ),
            m.LightSubConfig(what=[Light.LIVING_ROOM_FLOOR, Light.KITCHEN], state="on"),
            m.LightSubConfig(what=[Light.ENTANCE_DESK, Light.OFFICE_TABLE], state=1),
        )
    ),
    "TV Lights": m.AdHocRoutineConfig(
        items=(
            m.LightSubConfig(
                what=set(Light) - {Light.ENTANCE_DESK, Light.OFFICE_TABLE, Light.KITCHEN, Light.BEDROOM_NIGHTLIGHT},
                state="off",
            ),
            m.LightSubConfig(what=Light.KITCHEN, state="on"),
            m.LightSubConfig(what=[Light.ENTANCE_DESK, Light.OFFICE_TABLE], state=1),
        )
    ),
    "Front Rooms": m.AdHocRoutineConfig(
        items=(
            m.LightSubConfig(what=[Light.g_LAMP, Light.LIVING_ROOM_FLOOR], state="on"),
            m.LightSubConfig(what=Light.ENTANCE_DESK, state=100),
            m.LightSubConfig(what=[Light.OFFICE_DESK, Light.OFFICE_TABLE, Light.OFFICE_FLOOR, Light.KITCHEN], state="off"),
        )
    ),
    "Early Morning Lights": m.LightSubConfig(what=[Light.LIVING_ROOM_FLOOR, Light.KITCHEN], state="on"),
    "All Lights On": m.AdHocRoutineConfig(items=(m.LightSubConfig(what=Light, state="on"), m.LightSubConfig(what=Light, state=100))),
    "All Lights Off": m.AdHocRoutineConfig(items=(m.LightSubConfig(what=Light, state="off"),)),
    "Test": m.AdHocRoutineConfig(
        items=(
            m.LightSubConfig(what=Light, state="off"),
            m.LightSubConfig(what=Light, state="on"),
        )
    ),
}
