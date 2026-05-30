import os
from zoneinfo import ZoneInfo

from mistletoe import Document

from orc import model as m


class Config:
    OFF = "off"
    ON = "on"

    def __init__(self):
        self.orc_config = os.getenv("ORC_CONFIG", "src/config.md")
        self.jobs_db = os.getenv("ORC_DB", "sqlite:///jobs.sqlite")
        self.base_url = os.getenv("ORC_BASE_URL")
        self.http_timeout = int(os.getenv("ORC_HTTP_TIMEOUT", 5))
        self.http_ical_timeout = int(os.getenv("ORC_HTTP_ICAL_TIMEOUT", 120))
        self.tz = ZoneInfo(os.getenv("ORC_TZ", "America/New_York"))
        self.lat_long = (float(os.getenv("ORC_LAT", 40.7143)), float(os.getenv("ORC_LONG", -74.0060)))
        self.root_domain = os.getenv("ORC_ROOT_DOMAIN", "")
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
        self.virtual_devices = {e for cls in (Light, Sound) for e in cls if isinstance(e.value, int) and e.value < 0}
        self.people = m.build_people(doc, "People")
        self.themes = m.build_themes(doc, "Routines", "Themes", Light, Sound, self.people)
        self.schedule_routines = {r.name: r for e in self.themes.values() for r in e.configs}
        self.room_configs = m.build_config(doc, "Room Configs", Light, Sound, required=("Living Room",))
        self.ad_hoc_routines = m.build_config(doc, "Ad-Hoc Routines", Light, Sound)
        self.plugins = m.build_plugins(doc, "Super Routines")
        self.all_configs = self.ad_hoc_routines | self.room_configs
        self.room_configs_off = m.squish_configs(*self.room_configs.values(), state_override=self.OFF)
        self.button_highlight_configs = m.build_highlights(doc, "Button Highlights")
        self.durations = m.build_durations(doc, "Durations")
        self.default_config = self.room_configs["Living Room"]
        self.reset_config = m.squish_configs(m.Configs(*self.schedule_routines["Reset"].items))


config = Config()
