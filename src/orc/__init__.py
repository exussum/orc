import os
from zoneinfo import ZoneInfo

from mistletoe import Document

from orc import model as m

class Config:
    OFF = "off"
    ON = "on"

    def __init__(self):
        self.orc_config = os.getenv("ORC_CONFIG", "src/config.md")
        self.jobs_db = os.getenv("JOBS_DB", "sqlite:///jobs.sqlite")
        self.base_url = os.getenv("BASE_URL")
        self.http_timeout = int(os.getenv("HTTP_TIMEOUT", 5))
        self.http_ical_timeout = int(os.getenv("HTTP_ICAL_TIMEOUT", 120))
        self.tz = ZoneInfo(os.getenv("TZ", "America/New_York"))
        self.lat_long = (float(os.getenv("LAT", 40.7143)), float(os.getenv("LONG", -74.0060)))
        self.load(m.Secrets("", "", ""), {}, {})

    def load(self, secrets, hubitat_config, chromecast_config, enabled=False):
        self.enabled = enabled
        self.secrets = secrets
        with open(self.orc_config) as fh:
            doc = Document("".join(fh.readlines()))

        Light = m.build_enum(doc, "Devices", "Light", hubitat_config)
        Sound = m.build_enum(doc, "Devices", "Sound", chromecast_config)
        globals()["Light"] = Light
        globals()["Sound"] = Sound
        self.themes = m.build_themes(doc, "Routines", "Themes", Light, Sound)
        self.schedule_routines = {r.name: r for e in self.themes.values() for r in e.configs}
        self.room_configs = m.build_config(doc, "Room Configs", Light, Sound, required=("Living Room",))
        self.ad_hoc_routines = m.build_config(doc, "Ad-Hoc Routines", Light, Sound)
        self.super_routines = m.build_expr_config(doc, "Super Routines", Light, Sound)
        self.all_configs = self.super_routines | self.ad_hoc_routines | self.room_configs
        self.room_configs_off = m.squish_configs(*self.room_configs.values(), state_override=self.OFF)
        self.button_highlight_configs = m.build_highlights(doc, "Button Highlights")
        self.durations = m.build_durations(doc, "Durations")
        self.default_config = self.room_configs["Living Room"]
        self.reset_config = m.squish_configs(m.Configs(*self.schedule_routines["Reset"].items))

config = Config()
