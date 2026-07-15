import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from mistletoe import Document

from orc import device_registry
from orc import model as m

if TYPE_CHECKING:
    # Device enums are built at runtime in Config.load and attached to this package's
    # globals(). Declare them here so `orc.Light` etc. type-check across the codebase.
    Light: type[m.DeviceEnum]
    Chromecast: type[m.DeviceEnum]
    BroadLink: type[m.DeviceEnum]
    AC: type[m.DeviceEnum]

# Runtime-built device enum classes, repopulated on each config load. The parser reads
# this (as orc.device_enums) to resolve device columns mid-load, instead of scanning the
# package for enum classes.
device_enums: "list[type[m.DeviceEnum]]" = []


class Config:
    def __init__(self) -> None:
        self.config_dir = os.getenv("ORC_CONFIG_DIR", "src")
        self.jobs_db = os.getenv("ORC_DB", "sqlite:////tmp/jobs.sqlite")
        self.base_url = os.getenv("ORC_BASE_URL")
        self.internal_url = os.getenv("ORC_INTERNAL_URL", "http://example.test")
        self.http_timeout = int(os.getenv("ORC_HTTP_TIMEOUT", 5))
        self.http_ical_timeout = int(os.getenv("ORC_HTTP_ICAL_TIMEOUT", 120))
        self.tz = ZoneInfo(os.getenv("ORC_TZ", "America/New_York"))
        self.lat_long = (float(os.getenv("ORC_LAT", 40.7143)), float(os.getenv("ORC_LONG", -74.0060)))
        self.root_domain = os.getenv("ORC_ROOT_DOMAIN", "example.test")
        self.audio_device = os.getenv("ORC_AUDIO_DEVICE", "")
        self.load(m.Secrets("", "", ""), {})

    def load(self, secrets: m.Secrets, hubitat_config: dict[Any, tuple[Any, ...]]) -> None:
        self.secrets = secrets

        with open(Path(self.config_dir) / "config.md") as fh:
            doc = Document("".join(fh.readlines()))

        # Build plugins first so their register() hooks can append device types
        # (into the fresh registry state) before the enums below are built.
        self.plugins = m.build_plugins(doc, "Plugins")
        builder = device_registry.run_registration(p.func.__module__ for p in self.plugins.values())
        if "orc.api" in sys.modules:  # bootstrap load runs during `import orc`, before api is importable — and needs no dispatch
            sys.modules["orc.api"].register_core(builder)
        # Only Light carries a hubitat id_lookup; every other device type ignores it.
        enums = {
            name: m.build_enum(doc, "Devices", name, hubitat_config if name == "Light" else None, device_types=builder.device_types)
            for name in builder.device_types
        }
        globals().update(enums)
        globals()["device_enums"] = list(enums.values())
        self.registry = builder.build(enums)

        self.virtual_devices = {e for e in enums["Light"] if isinstance(e.value, int) and e.value < 0}
        self.people = m.build_people(doc, "People")
        self.themes = m.build_themes(doc, "Routines", "Themes", self.people)
        self.schedule_routines = {r.name: r for e in self.themes.values() for r in e.configs}
        self.room_configs = m.build_config(doc, "Room Configs", required=("Living Room",))
        self.ad_hoc_routines = m.build_ad_hoc_routines(doc, "Ad-Hoc Routines")
        self.room_configs_off = m.squish_configs(*self.room_configs.values(), state_override=m.OFF)
        self.button_highlight_configs = m.build_highlights(doc, "Button Highlights")
        self.audio_volumes = m.build_audio_volumes(doc, "Audio Volumes", required=(m.AUDIO_INFO, m.AUDIO_FATAL))
        self.default_config = self.room_configs["Living Room"]
        excluded = {name for name, d in self.registry.devices.items() if d.reset_excluded}
        reset_items = self.schedule_routines["Reset"].items
        self.reset_config = m.squish_configs(
            m.Configs(*(i for i in reset_items if (i.what if isinstance(i.what, type) else type(i.what)).__name__ not in excluded))
        )


config = Config()
