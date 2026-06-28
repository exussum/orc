import os
from zoneinfo import ZoneInfo

from mistletoe import Document

from orc import model as m


class Config:
    OFF = "off"
    ON = "on"
    STOP = "stop"
    FOLLOW = "follow"
    THEME_WORK_DAY = "work day"
    THEME_DAY_OFF = "day off"
    AUDIO_INFO = "INFO"
    AUDIO_FATAL = "FATAL"

    def __init__(self):
        self.orc_config = os.getenv("ORC_CONFIG", "src/config.md")
        self.jobs_db = os.getenv("ORC_DB", "sqlite:////tmp/jobs.sqlite")
        self.base_url = os.getenv("ORC_BASE_URL")
        self.internal_url = os.getenv("ORC_INTERNAL_URL", "")
        self.http_timeout = int(os.getenv("ORC_HTTP_TIMEOUT", 5))
        self.http_ical_timeout = int(os.getenv("ORC_HTTP_ICAL_TIMEOUT", 120))
        self.tz = ZoneInfo(os.getenv("ORC_TZ", "America/New_York"))
        self.lat_long = (float(os.getenv("ORC_LAT", 40.7143)), float(os.getenv("ORC_LONG", -74.0060)))
        self.root_domain = os.getenv("ORC_ROOT_DOMAIN", "")
        self.audio_device = os.getenv("ORC_AUDIO_DEVICE", "")
        self.load(m.Secrets("", "", "", "", ""), {})

    def load(self, secrets, hubitat_config):
        self.secrets = secrets

        with open(self.orc_config) as fh:
            doc = Document("".join(fh.readlines()))

        Light = m.build_enum(doc, "Devices", "Light", hubitat_config)
        Chromecast = m.build_enum(doc, "Devices", "Chromecast")
        BroadLink = m.build_enum(doc, "Devices", "BroadLink")
        WebOS = m.build_enum(doc, "Devices", "WebOS")
        LGTV = m.build_enum(doc, "Devices", "LGTV")
        Leak = m.build_enum(doc, "Devices", "Leak")
        AC = m.build_enum(doc, "Devices", "AC")
        globals()["Light"] = Light
        globals()["Chromecast"] = Chromecast
        globals()["BroadLink"] = BroadLink
        globals()["WebOS"] = WebOS
        globals()["Leak"] = Leak
        globals()["AC"] = AC
        globals()["LGTV"] = LGTV
        self.virtual_devices = {e for e in Light if isinstance(e.value, int) and e.value < 0}
        self.people = m.build_people(doc, "People")
        self.themes = m.build_themes(doc, "Routines", "Themes", Light, Chromecast, LGTV, self.people)
        self.schedule_routines = {r.name: r for e in self.themes.values() for r in e.configs}
        self.room_configs = m.build_config(doc, "Room Configs", Light, Chromecast, LGTV, required=("Living Room",))
        self.ad_hoc_routines = m.build_config(doc, "Ad-Hoc Routines", Light, Chromecast, LGTV)
        self.plugins = m.build_plugins(doc, "Plugins")
        self.all_configs = self.ad_hoc_routines | self.room_configs
        self.room_configs_off = m.squish_configs(*self.room_configs.values(), state_override=self.OFF)
        self.button_highlight_configs = m.build_highlights(doc, "Button Highlights")
        self.audio_volumes = m.build_audio_volumes(doc, "Audio Volumes", required=(self.AUDIO_INFO, self.AUDIO_FATAL))
        self.default_config = self.room_configs["Living Room"]
        self.reset_config = m.squish_configs(m.Configs(*(i for i in self.schedule_routines["Reset"].items if i.what is not WebOS)))


config = Config()
