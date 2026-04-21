import os
from zoneinfo import ZoneInfo

from mistletoe import Document

from orc import dal
from orc import model as m

HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", 5))
HTTP_ICAL_TIMEOUT = int(os.getenv("HTTP_ICAL_TIMEOUT", 120))

BASE_URL = os.getenv("BASE_URL", "http://base.example.com")
ACCESS_TOKEN = "?access_token=" + os.getenv("ACCESS_TOKEN", "example-token")
MARKET_HOLIDAYS_URL = os.getenv("MARKET_HOLIDAYS_URL", "http://holidays.exmaple.com")
SSL_KEY = os.getenv("SSL_KEY", "")
SSL_CERT = os.getenv("SSL_CERT", "")
ORC_CONFIG = os.getenv("ORC_CONFIG", "src/config.md")
ENABLED = os.getenv("ENABLED", "")
ICS_URL = os.getenv("ICS_URL", "")

hubitat_config = dal.get_config() if ENABLED else {}

TZ = ZoneInfo("America/New_York")
LAT_LONG = (40.7143, -74.0060)
OFF = "off"
ON = "on"

with open(ORC_CONFIG) as fh:
    doc = Document("".join(fh.readlines()))

Light = m.build_enum(doc, "Devices", "Light", hubitat_config)
Sound = m.build_enum(doc, "Devices", "Sound", hubitat_config)

THEMES = m.build_themes(doc, "Routines", "Themes", Light, Sound)
SCHEDULE_ROUTINES = {r.name: r for e in THEMES for r in e.configs}
ROOM_CONFIGS = m.build_config(doc, "Room Configs", Light, Sound)
AD_HOC_ROUTINES = m.build_config(doc, "Ad-Hoc Routines", Light, Sound)
SUPER_ROUTINES = m.build_expr_config(doc, "Super Routines", Light, Sound)
ALL_CONFIGS = SUPER_ROUTINES | AD_HOC_ROUTINES | ROOM_CONFIGS

ROOM_CONFIGS_OFF = m.squish_configs(*ROOM_CONFIGS.values(), state_override=OFF)
BUTTON_HIGHLIGHT_CONFIGS = m.build_highlights(doc, "Button Highlights")

DEFAULT_CONFIG = ROOM_CONFIGS["Living Room"]
RESET_CONFIG = m.squish_configs(m.Configs(*SCHEDULE_ROUTINES["Reset"].items))
