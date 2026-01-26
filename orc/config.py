import itertools
import os
from dataclasses import replace
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
        {token: id_lookup.get(name, -(default + 1)) for (default, (name, token)) in enumerate(hub_name_to_token.items())},
    )


Light = build_enum(
    "Light",
    {
        "bedroom night light": "BEDROOM_NIGHTLIGHT",
        "bedroom lamp": "BEDROOM_LAMP",
        "entrance desk lamp": "ENTANCE_DESK",
        "entrance bulb 1": "ENTRANCE_BULB_1",
        "entrance bulb 2": "ENTRANCE_BULB_2",
        "living room desk lamp": "LIVING_ROOM_DESK",
        "living room floor lamp": "LIVING_ROOM_FLOOR",
        "kitchen lights": "KITCHEN_CABINET",
        "kitchen overhead": "KITCHEN_OVERHEAD",
        "office floor lamp": "OFFICE_FLOOR",
        "office desk lamp": "OFFICE_DESK",
        "office table lamp": "OFFICE_TABLE",
    },
    hubitat_config,
)

Sound = build_enum(
    "Sound",
    {
        "Living room mini": "LIVING_ROOM_MINI",
        "Bedroom display": "BEDROOM_DISPLAY",
        "Office display": "OFFICE_DISPLAY",
        "Kitchen mini": "KITCHEN_CABINET_MINI",
    },
    hubitat_config,
)

OFF = "off"
ON = "on"


CONFIG_RESET_LIGHT = m.Routine(
    "reset",
    "1:00",
    (m.Config(set(Light) - {Light.BEDROOM_NIGHTLIGHT}, OFF, mandatory=True),),
)

CONFIG_PARTNER_UP = m.Routine(
    "partner up",
    "6:15",
    (m.Config([Light.LIVING_ROOM_FLOOR, Light.KITCHEN_CABINET], ON),),
)

CONFIG_SUNRISE_LIGHTS = m.Routine("sunrise lights", "sunrise", (m.Config([Light.BEDROOM_NIGHTLIGHT, Light.KITCHEN_CABINET], OFF, mandatory=True),))

CONFIG_UP_AND_ATOM = m.Routine(
    "up and atom",
    "9:00",
    (
        m.Config([Light.ENTANCE_DESK, Light.OFFICE_TABLE], 100),
        m.Config([Light.LIVING_ROOM_DESK, Light.LIVING_ROOM_FLOOR], ON),
        m.Config(Sound, 40),
        m.Config([Light.OFFICE_TABLE, Light.BEDROOM_NIGHTLIGHT], OFF),
    ),
)

CONFIG_SUNSET_LIGHTS = m.Routine(
    "sunset lights",
    "sunset",
    (
        m.Config(
            [Light.BEDROOM_NIGHTLIGHT, Light.KITCHEN_CABINET],
            ON,
            mandatory=True,
        ),
    ),
)

CONFIG_QUIET_TIME = m.Routine("quiet time", "23:00", (m.Config(Sound, 10, mandatory=True),))

CONFIG_PARTNER_LEAVING = m.Routine(
    "partner leaving",
    "7:00",
    (m.Config(Light.ENTANCE_DESK, 1),),
)

CONFIGS = m.scan(
    m.Theme(
        name="work day",
        configs=(
            CONFIG_RESET_LIGHT,
            replace(CONFIG_PARTNER_UP, when="6:15"),
            CONFIG_PARTNER_LEAVING,
            CONFIG_SUNRISE_LIGHTS,
            replace(CONFIG_UP_AND_ATOM, when="9:00"),
            CONFIG_SUNSET_LIGHTS,
            CONFIG_QUIET_TIME,
        ),
    ),
    m.Theme(name="away", configs=(CONFIG_RESET_LIGHT,)),
    m.Theme(
        name="babysitter",
        configs=(
            m.Routine(
                "nightlight off",
                "sunrise",
                (m.Config(Light.BEDROOM_NIGHTLIGHT, OFF),),
            ),
            m.Routine(name="nightlight on", when="sunset", items=(m.Config(Light.BEDROOM_NIGHTLIGHT, ON),)),
        ),
    ),
    m.Theme(
        name="home alone",
        configs=(
            CONFIG_RESET_LIGHT,
            CONFIG_SUNRISE_LIGHTS,
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
            CONFIG_SUNRISE_LIGHTS,
            replace(CONFIG_UP_AND_ATOM, when="9:30"),
            CONFIG_SUNSET_LIGHTS,
            CONFIG_QUIET_TIME,
        ),
    ),
)

ROOM_CONFIGS = {
    "Living Room": m.Configs(
        (
            m.Config((Light.LIVING_ROOM_FLOOR, Light.LIVING_ROOM_DESK), ON),
            m.Config(Light.ENTANCE_DESK, 100),
        ),
    ),
    "Office": m.Configs(
        (
            m.Config(Light.OFFICE_FLOOR, ON),
            m.Config(Light.OFFICE_DESK, 35),
            m.Config(Light.OFFICE_TABLE, 100),
        )
    ),
    "Kitchen": m.Configs((m.Config((Light.KITCHEN_CABINET, Light.KITCHEN_OVERHEAD), ON),)),
    "Bedroom": m.Configs((m.Config(Light.BEDROOM_LAMP, ON),)),
}

ROOM_CONFIGS_OFF = m.Configs(
    (
        m.Config(
            (
                Light.LIVING_ROOM_FLOOR,
                Light.LIVING_ROOM_DESK,
                Light.ENTANCE_DESK,
                Light.OFFICE_FLOOR,
                Light.OFFICE_DESK,
                Light.OFFICE_TABLE,
                Light.KITCHEN_CABINET,
                Light.KITCHEN_OVERHEAD,
                Light.BEDROOM_LAMP,
            ),
            OFF,
        ),
    )
)

THEME_CONFIGS = {
    "Bed Time": m.Configs(
        (
            m.Config(
                set(Light)
                - {
                    Light.BEDROOM_NIGHTLIGHT,
                },
                OFF,
            ),
            m.Config(Light.BEDROOM_NIGHTLIGHT, ON),
        )
    ),
    "Partial TV Lights": m.Configs(
        (
            m.Config(
                set(Light)
                - {
                    Light.ENTANCE_DESK,
                    Light.OFFICE_TABLE,
                    Light.KITCHEN_CABINET,
                    Light.LIVING_ROOM_FLOOR,
                    Light.BEDROOM_NIGHTLIGHT,
                },
                OFF,
            ),
            m.Config((Light.LIVING_ROOM_FLOOR, Light.KITCHEN_CABINET), ON),
            m.Config((Light.ENTANCE_DESK, Light.OFFICE_TABLE), 1),
        )
    ),
    "TV Lights": m.Configs(
        (
            m.Config(
                set(Light) - {Light.ENTANCE_DESK, Light.OFFICE_TABLE, Light.KITCHEN_CABINET, Light.BEDROOM_NIGHTLIGHT},
                OFF,
            ),
            m.Config(Light.KITCHEN_CABINET, ON),
            m.Config((Light.ENTANCE_DESK, Light.OFFICE_TABLE), 1),
        )
    ),
    "Early Morning Lights": m.Configs((m.Config([Light.LIVING_ROOM_FLOOR, Light.KITCHEN_CABINET], ON),)),
}

OTHER_CONFIGS = {
    "All Lights On": m.Configs((m.Config(Light, ON), m.Config(Light, 100))),
    "All Lights Off": m.Configs((m.Config(Light, OFF),)),
    "Video Conference": m.Configs(
        (
            m.Config(Light.OFFICE_TABLE, OFF),
            m.Config(Light.OFFICE_FLOOR, ON),
            m.Config(Light.OFFICE_DESK, 50),
        )
    ),
    "Test": m.Configs(tuple(m.Config(e, s) for (e, s) in tuple(itertools.product(list(Light), [ON, OFF])))),
}

DEFAULT_CONFIG = ROOM_CONFIGS["Living Room"]
ALL_CONFIGS = OTHER_CONFIGS | THEME_CONFIGS | ROOM_CONFIGS
