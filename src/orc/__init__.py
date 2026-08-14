import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

from command_cfg import ConfigError

from orc import model as m
from orc.declarations import collect_declarations
from orc.loader import parse_config, validate

Light: type[m.DeviceEnum]
Chromecast: type[m.DeviceEnum]
BroadLink: type[m.DeviceEnum]
AC: type[m.DeviceEnum]


class Config:
    def __init__(self) -> None:
        # visible as orc.config before the parse below: modules imported by
        # `plugin`/`provider` config lines read it at import time
        globals()["config"] = self
        self.config_dir = os.getenv("ORC_CONFIG_DIR", "src")
        self.load(m.Secrets(), {})

    def load(self, secrets: m.Secrets, zigbee_config: dict[Any, tuple[Any, ...]]) -> None:
        self.secrets = secrets
        plugins_dir = Path(self.config_dir) / "plugins"
        self.plugin_configs = {p.relative_to(plugins_dir).with_suffix("").as_posix(): p.read_text() for p in plugins_dir.glob("**/*.orc")}

        parsed = parse_config((Path(self.config_dir) / "config.orc").read_text(), zigbee_config)
        validate(parsed)
        self._install(parsed)

        self.default_config = self.routines["ROUTINE_DEFAULT"]
        self.reset_config = self.routines["ROUTINE_RESET"]
        self.schedule_routines = {r.name: r for theme in self.themes.values() for r in theme.configs}
        self.room_configs_off = m.squish_configs(*self.room_configs.values(), state_override=m.OFF)

    def plugin(self, id: str) -> m.Plugin | None:
        return next((p for p in self.plugins if p.name == id), None)

    def plugins_in(self, section: str) -> tuple[m.Plugin, ...]:
        return tuple(p for p in self.plugins if p.section == section)

    def plugin_for(self, module: ModuleType) -> m.Plugin:
        plugin = next((p for p in self.plugins if p.module is module), None)
        if plugin is None:
            raise ConfigError(f"No plugin line configured for module {module.__name__!r}")
        return plugin

    def _install(self, parsed: SimpleNamespace) -> None:
        self.settings = parsed.settings
        self.plugins = parsed.plugins
        declarations = collect_declarations(parsed.plugin_modules)

        if "orc.api" in sys.modules:  # bootstrap load runs during `import orc`, before api is importable — and needs no dispatch
            sys.modules["orc.api"].declare_core(declarations)

        globals().update(parsed.enums)
        self.registry = declarations.build(parsed.enums)
        self.virtual_devices = {e for e in parsed.enums["Light"] if isinstance(e.value, int) and e.value < 0}

        self.people = parsed.people
        self.providers = parsed.providers
        self.routines = parsed.routines
        self.themes = parsed.themes
        self.room_configs = parsed.room_configs
        self.ad_hoc_routines = parsed.ad_hoc_routines
        self.buttons = parsed.buttons
        self.button_highlight_configs = parsed.button_highlight_configs
        self.audio_volumes = parsed.audio_volumes


config = Config()
