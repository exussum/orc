import os
from zoneinfo import ZoneInfo

from mistletoe import Document

from orc import dal
from orc import model as m

BASE_URL = os.getenv("BASE_URL", "http://base.example.com")
ACCESS_TOKEN = "?access_token=" + os.getenv("ACCESS_TOKEN", "example-token")
SUNRISE_URL = os.getenv("SUNRISE_URL", "http://sunrise.example.com")
MARKET_HOLIDAYS_URL = os.getenv("MARKET_HOLIDAYS_URL", "http://holidays.exmaple.com")
SSL_KEY = os.getenv("SSL_KEY", "")
SSL_CERT = os.getenv("SSL_CERT", "")
ORC_CONFIG = os.getenv("ORC_CONFIG", "config.md")
ENABLED = os.getenv("ENABLED", "")

hubitat_config = dal.get_config() if ENABLED else {}

TZ = ZoneInfo("America/New_York")
OFF = "off"
ON = "on"

with open(ORC_CONFIG) as fh:
    doc = Document("".join(fh.readlines()))

Light = m.build_enum(doc, "Devices", "Light", hubitat_config)
Sound = m.build_enum(doc, "Devices", "Sound", hubitat_config)

THEMES = m.build_themes(doc, "Routines", "Themes", Light, Sound)
ROOM_CONFIGS = m.build_config(doc, "Room Configs", Light, Sound)
THEME_CONFIGS = m.build_config(doc, "Ad-Hoc Routines", Light, Sound)
OTHER_CONFIGS = m.build_expr_config(doc, "Super Routines", Light, Sound)
ALL_CONFIGS = OTHER_CONFIGS | THEME_CONFIGS | ROOM_CONFIGS

ROOM_CONFIGS_OFF = m.squish_configs(*ROOM_CONFIGS.values(), state_override=OFF)
SCHEDULE_ROUTINES = {r.name: r for e in THEMES for r in e.configs}

DEFAULT_CONFIG = ROOM_CONFIGS["Living Room"]
RESET_CONFIG = m.squish_configs(m.Configs(*SCHEDULE_ROUTINES["Reset"].items))
