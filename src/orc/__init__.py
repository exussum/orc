import os
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from mistletoe import Document

from orc import model as m
from orc.declarations import collect_declarations

Light: type[m.DeviceEnum]
Chromecast: type[m.DeviceEnum]
BroadLink: type[m.DeviceEnum]
AC: type[m.DeviceEnum]

device_enums: list[type[m.DeviceEnum]] = []


def _parse_doc(path: Path) -> Document:
    with open(path) as fh:
        return Document(fh)


class Config:
    def __init__(self) -> None:
        self.config_dir = os.getenv("ORC_CONFIG_DIR", "src")
        self.jobs_db = os.getenv("ORC_DB", "sqlite:////tmp/jobs.sqlite")
        self.hubitat_url = os.getenv("ORC_HUBITAT_URL")
        self.internal_url = os.getenv("ORC_INTERNAL_URL", "http://example.test")
        self.http_timeout = int(os.getenv("ORC_HTTP_TIMEOUT", 5))
        self.http_ical_timeout = int(os.getenv("ORC_HTTP_ICAL_TIMEOUT", 120))
        self.tz = ZoneInfo(os.getenv("ORC_TZ", "America/New_York"))
        self.lat_long = (float(os.getenv("ORC_LAT", 40.7143)), float(os.getenv("ORC_LONG", -74.0060)))
        self.root_domain = os.getenv("ORC_ROOT_DOMAIN", "example.test")
        self.audio_device = os.getenv("ORC_AUDIO_DEVICE", "")
        self.load(m.Secrets("", "", ""), {})

    def load(self, secrets: m.Secrets, zigbee_config: dict[Any, tuple[Any, ...]]) -> None:
        self.secrets = secrets
        doc, self.plugin_docs = self._parse_config_docs()

        self._install_devices(doc, zigbee_config)

        self.people = m.build_people(doc, "People")
        self.themes = m.build_themes(doc, "Routines", "Themes", self.people)
        self.routines = m.build_routines(doc, "Routines", required=("ROUTINE_DEFAULT", "ROUTINE_RESET"))
        self.room_configs = m.build_config(doc, "Room Configs")
        self.ad_hoc_routines = m.build_ad_hoc_routines(doc, "Ad-Hoc Routines")
        self.buttons = m.build_buttons(doc, "Button Mapping")
        self.button_highlight_configs = m.build_highlights(doc, "Button Highlights")
        self.audio_volumes = m.build_audio_volumes(doc, "Audio Volumes", required=(m.AUDIO_INFO, m.AUDIO_FATAL))

        self.default_config = self.routines["ROUTINE_DEFAULT"]
        self.reset_config = self.routines["ROUTINE_RESET"]
        self.schedule_routines = {r.name: r for theme in self.themes.values() for r in theme.configs}
        self.room_configs_off = m.squish_configs(*self.room_configs.values(), state_override=m.OFF)

    def _parse_config_docs(self) -> tuple[Document, dict[str, Document]]:
        plugins_dir = Path(self.config_dir) / "plugins"
        plugin_docs = {p.relative_to(plugins_dir).with_suffix("").as_posix(): _parse_doc(p) for p in plugins_dir.glob("**/*.md")}
        return _parse_doc(Path(self.config_dir) / "config.md"), plugin_docs

    def _install_devices(self, doc: Document, zigbee_config: dict[Any, tuple[Any, ...]]) -> None:
        """Build the device enums and registry, installing the enums on this package —
        every builder that parses a Device column resolves it through ``orc.device_enums``,
        so this must run before any other section builds."""
        self.plugins = m.build_plugins(doc, "Plugins")
        declarations = collect_declarations(p.func.__module__ for p in self.plugins.values())
        if "orc.api" in sys.modules:  # bootstrap load runs during `import orc`, before api is importable — and needs no dispatch
            sys.modules["orc.api"].declare_core(declarations)

        enums = {
            name: m.build_enum(
                doc, "Devices", name, zigbee_config if name in ("Light", "Button") else None, device_types=declarations.device_types
            )
            for name in dict.fromkeys((*declarations.device_types, *m.device_types_in(doc, "Devices")))
        }
        globals().update(enums)
        globals()["device_enums"] = list(enums.values())
        self.registry = declarations.build(enums)
        self.virtual_devices = {e for e in enums["Light"] if isinstance(e.value, int) and e.value < 0}


config = Config()
