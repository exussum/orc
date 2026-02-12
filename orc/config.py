import itertools
import os
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from orc import dal
from orc.model import Config, Configs, Routine, Theme, build_enum, scan

BASE_URL = os.getenv("BASE_URL", "http://base.example.com")
ACCESS_TOKEN = "?access_token=" + os.getenv("ACCESS_TOKEN", "example-token")
SUNRISE_URL = os.getenv("SUNRISE_URL", "http://sunrise.example.com")
MARKET_HOLIDAYS_URL = os.getenv("MARKET_HOLIDAYS_URL", "http://holidays.exmaple.com")
SSL_KEY = os.getenv("SSL_KEY", "")
SSL_CERT = os.getenv("SSL_CERT", "")
ENABLED = os.getenv("ENABLED", "")

hubitat_config = dal.get_config() if ENABLED else {}

TZ = ZoneInfo("America/New_York")

OFF = "off"
ON = "on"

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

ROUTINE_RESET_LIGHT = Routine("Reset", "1:00", (Config(Light - {Light.BEDROOM_NIGHTLIGHT}, OFF, mandatory=True),))
ROUTINE_PARTNER_UP = Routine("Partner up", "6:15", (Config({Light.LIVING_ROOM_FLOOR, Light.KITCHEN_CABINET}, ON),))
ROUTINE_SUNRISE_LIGHTS = Routine(
    "Sunrise Lights", "sunrise", (Config({Light.BEDROOM_NIGHTLIGHT, Light.KITCHEN_CABINET}, OFF, mandatory=True),)
)

ROUTINE_UP_AND_ATOM = Routine(
    "Up and Atom",
    "9:00",
    (
        Config({Light.ENTANCE_DESK, Light.OFFICE_TABLE}, 100),
        Config({Light.LIVING_ROOM_DESK, Light.LIVING_ROOM_FLOOR}, ON),
        Config(Sound, 40),
        Config({Light.OFFICE_TABLE, Light.BEDROOM_NIGHTLIGHT}, OFF),
    ),
)

ROUTINE_SUNSET_LIGHTS = Routine(
    "Sunset Lights",
    "sunset",
    (
        Config(
            {Light.BEDROOM_NIGHTLIGHT, Light.KITCHEN_CABINET},
            ON,
            mandatory=True,
        ),
    ),
)
ROUTINE_QUIET_TIME = Routine("Quiet Time", "23:00", (Config(Sound, 10, mandatory=True),))
ROUTINE_PARTNER_LEAVING = Routine("Partner Leaving", "7:00", (Config(Light.ENTANCE_DESK, 1),))
ROUTINE_NIGHTLIGHT_OFF = Routine("Nightlight Off", "sunrise", (Config(Light.BEDROOM_NIGHTLIGHT, OFF),))
ROUTINE_NIGHTLIGHT_ON = Routine("Nightlight On", "sunset", (Config(Light.BEDROOM_NIGHTLIGHT, ON),))

THEMES = scan(
    Theme(
        "work day",
        ROUTINE_RESET_LIGHT,
        replace(ROUTINE_PARTNER_UP, when="6:15"),
        ROUTINE_PARTNER_LEAVING,
        ROUTINE_SUNRISE_LIGHTS,
        replace(ROUTINE_UP_AND_ATOM, when="9:00"),
        ROUTINE_SUNSET_LIGHTS,
        ROUTINE_QUIET_TIME,
    ),
    Theme("away", ROUTINE_RESET_LIGHT),
    Theme(
        "babysitter",
        ROUTINE_NIGHTLIGHT_OFF,
        ROUTINE_NIGHTLIGHT_ON,
    ),
    Theme(
        "home alone",
        ROUTINE_RESET_LIGHT,
        ROUTINE_SUNRISE_LIGHTS,
        replace(ROUTINE_UP_AND_ATOM, when="9:30"),
        ROUTINE_SUNSET_LIGHTS,
        ROUTINE_QUIET_TIME,
    ),
    Theme(
        "day off",
        ROUTINE_RESET_LIGHT,
        replace(ROUTINE_PARTNER_UP, when="7:00"),
        ROUTINE_SUNRISE_LIGHTS,
        replace(ROUTINE_UP_AND_ATOM, when="9:30"),
        ROUTINE_SUNSET_LIGHTS,
        ROUTINE_QUIET_TIME,
    ),
)

ROOM_CONFIGS = {
    "Living Room": Configs(
        Config((Light.LIVING_ROOM_FLOOR, Light.LIVING_ROOM_DESK), ON),
        Config(Light.ENTANCE_DESK, 100),
    ),
    "Office": Configs(
        Config(Light.OFFICE_FLOOR, ON),
        Config(Light.OFFICE_DESK, 35),
        Config(Light.OFFICE_TABLE, 100),
    ),
    "Kitchen": Configs(Config((Light.KITCHEN_CABINET, Light.KITCHEN_OVERHEAD), ON)),
    "Bedroom": Configs(Config(Light.BEDROOM_LAMP, ON)),
}

THEME_CONFIGS = {
    "Bed Time": Configs(
        Config(Light - {Light.BEDROOM_NIGHTLIGHT}, OFF),
        Config(Light.BEDROOM_NIGHTLIGHT, ON),
    ),
    "Partial TV Lights": Configs(
        Config(
            Light
            - {
                Light.ENTANCE_DESK,
                Light.OFFICE_TABLE,
                Light.KITCHEN_CABINET,
                Light.LIVING_ROOM_FLOOR,
                Light.BEDROOM_NIGHTLIGHT,
            },
            OFF,
        ),
        Config((Light.LIVING_ROOM_FLOOR, Light.KITCHEN_CABINET), ON),
        Config((Light.ENTANCE_DESK, Light.OFFICE_TABLE), 1),
    ),
    "TV Lights": Configs(
        Config(
            Light - {Light.ENTANCE_DESK, Light.OFFICE_TABLE, Light.KITCHEN_CABINET, Light.BEDROOM_NIGHTLIGHT},
            OFF,
        ),
        Config(Light.KITCHEN_CABINET, ON),
        Config((Light.ENTANCE_DESK, Light.OFFICE_TABLE), 1),
    ),
    "Early Morning Lights": Configs(Config({Light.LIVING_ROOM_FLOOR, Light.KITCHEN_CABINET}, ON)),
}

OTHER_CONFIGS = {
    "All Lights On": Configs(Config(Light, ON), Config(Light, 100)),
    "All Lights Off": Configs(Config(Light, OFF)),
    "Video Conference": Configs(
        Config(Light.OFFICE_TABLE, 5),
        Config(Light.OFFICE_FLOOR, ON),
        Config(Light.OFFICE_DESK, 50),
    ),
    "Test": Configs(*(Config(e, s) for (e, s) in tuple(itertools.product(Light, [ON, OFF])))),
    "Restore Snapshot": None,
    "Back on Schedule": None,
}

ROOM_CONFIGS_OFF = Configs(
    Config(
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
    )
)

SCHEDULE_ROUTINES = {
    e.name: e
    for e in (
        ROUTINE_RESET_LIGHT,
        ROUTINE_PARTNER_UP,
        ROUTINE_PARTNER_LEAVING,
        ROUTINE_SUNRISE_LIGHTS,
        ROUTINE_UP_AND_ATOM,
        ROUTINE_SUNSET_LIGHTS,
        ROUTINE_QUIET_TIME,
        ROUTINE_NIGHTLIGHT_ON,
        ROUTINE_NIGHTLIGHT_OFF,
    )
}

DEFAULT_CONFIG = ROOM_CONFIGS["Living Room"]
ALL_CONFIGS = OTHER_CONFIGS | THEME_CONFIGS | ROOM_CONFIGS
