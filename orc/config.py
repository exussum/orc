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


CONFIG_RESET_LIGHT = m.RoutineConfig(
    name="reset",
    when="1:00",
    items=(m.Config(what=set(Light) - {Light.BEDROOM_NIGHTLIGHT}, state=OFF, mandatory=True),),
)

CONFIG_PARTNER_UP = m.RoutineConfig(
    name="partner up",
    when="6:15",
    items=(m.Config(what=[Light.LIVING_ROOM_FLOOR, Light.KITCHEN_CABINET], state=ON),),
)

CONFIG_UP_AND_ATOM = m.RoutineConfig(
    name="up and atom",
    when="9:00",
    items=(
        m.Config(what=[Light.ENTANCE_DESK, Light.OFFICE_TABLE], state=100),
        m.Config(what=[Light.LIVING_ROOM_DESK, Light.LIVING_ROOM_FLOOR], state=ON),
        m.Config(what=[Light.BEDROOM_NIGHTLIGHT, Light.KITCHEN_CABINET], state=OFF),
        m.Config(what=Sound, state=40),
        m.Config(what=[Light.OFFICE_TABLE, Light.BEDROOM_NIGHTLIGHT], state=OFF),
    ),
)

CONFIG_SUNSET_LIGHTS = m.RoutineConfig(
    name="sunset lights",
    when="sunset",
    items=(
        m.Config(
            what=[Light.BEDROOM_NIGHTLIGHT, Light.KITCHEN_CABINET],
            state=ON,
            mandatory=True,
        ),
    ),
)

CONFIG_QUIET_TIME = m.RoutineConfig(name="quiet time", when="23:00", items=(m.Config(what=Sound, state=10, mandatory=True),))

CONFIG_PARTNER_LEAVING = m.RoutineConfig(
    name="partner leaving",
    when="7:00",
    items=(
        m.Config(what=Light.ENTANCE_DESK, state=1),
        m.Config(what=Light.KITCHEN_CABINET, state=OFF),
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
            m.RoutineConfig(
                name="nightlight off",
                when="sunrise",
                items=(m.Config(what=Light.BEDROOM_NIGHTLIGHT, state=OFF),),
            ),
            m.RoutineConfig(name="nightlight on", when="sunset", items=(m.Config(what=Light.BEDROOM_NIGHTLIGHT, state=ON),)),
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

ROOM_CONFIGS = {
    "Living Room": m.AdHocConfig(
        items=(
            m.Config(what=(Light.LIVING_ROOM_FLOOR, Light.LIVING_ROOM_DESK), state=ON),
            m.Config(what=Light.ENTANCE_DESK, state=100),
        ),
    ),
    "Office": m.AdHocConfig(
        items=(
            m.Config(what=Light.OFFICE_FLOOR, state=ON),
            m.Config(what=Light.OFFICE_DESK, state=50),
            m.Config(what=Light.OFFICE_TABLE, state=100),
        )
    ),
    "Kitchen": m.AdHocConfig(items=(m.Config(what=(Light.KITCHEN_CABINET, Light.KITCHEN_OVERHEAD), state=ON),)),
    "Bedroom": m.AdHocConfig(items=(m.Config(what=Light.BEDROOM_LAMP, state=ON),)),
}

THEME_CONFIGS = {
    "Bed Time": m.AdHocConfig(
        items=(
            m.Config(
                what=set(Light)
                - {
                    Light.BEDROOM_NIGHTLIGHT,
                },
                state=OFF,
            ),
            m.Config(what=Light.BEDROOM_NIGHTLIGHT, state=ON),
        )
    ),
    "Partial TV Lights": m.AdHocConfig(
        items=(
            m.Config(
                what=set(Light)
                - {
                    Light.ENTANCE_DESK,
                    Light.OFFICE_TABLE,
                    Light.KITCHEN_CABINET,
                    Light.LIVING_ROOM_FLOOR,
                    Light.BEDROOM_NIGHTLIGHT,
                },
                state=OFF,
            ),
            m.Config(what=[Light.LIVING_ROOM_FLOOR, Light.KITCHEN_CABINET], state=ON),
            m.Config(what=[Light.ENTANCE_DESK, Light.OFFICE_TABLE], state=1),
        )
    ),
    "TV Lights": m.AdHocConfig(
        items=(
            m.Config(
                what=set(Light) - {Light.ENTANCE_DESK, Light.OFFICE_TABLE, Light.KITCHEN_CABINET, Light.BEDROOM_NIGHTLIGHT},
                state=OFF,
            ),
            m.Config(what=Light.KITCHEN_CABINET, state=ON),
            m.Config(what=[Light.ENTANCE_DESK, Light.OFFICE_TABLE], state=1),
        )
    ),
    "Early Morning Lights": m.AdHocConfig(items=(m.Config(what=[Light.LIVING_ROOM_FLOOR, Light.KITCHEN_CABINET], state=ON),)),
}

OTHER_CONFIGS = {
    "All Lights On": m.AdHocConfig(items=(m.Config(what=Light, state=ON), m.Config(what=Light, state=100))),
    "All Lights Off": m.AdHocConfig(items=(m.Config(what=Light, state=OFF),)),
    "Video Conference": m.AdHocConfig(
        items=(
            m.Config(what=Light.OFFICE_TABLE, state=OFF),
            m.Config(what=Light.OFFICE_FLOOR, state=ON),
            m.Config(what=Light.OFFICE_DESK, state=50),
        )
    ),
    "Test": m.AdHocConfig(items=tuple(m.Config(what=e, state=s) for (e, s) in tuple(itertools.product(list(Light), [ON, OFF])))),
}

DEFAULT_CONFIG = ROOM_CONFIGS["Living Room"]
