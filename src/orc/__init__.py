import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

from command_cfg import ConfigError

from orc import model as m
from orc.collections import of_type
from orc.dal.secrets import stub as secrets_stub
from orc.kernel.declarations import collect_declarations
from orc.kernel.loader import ble_keys, check_secrets, parse_config, secret_needs, validate

Light: type[m.DeviceEnum] = m.DeviceEnum("Light", {}, module="orc")  # type: ignore[call-arg,arg-type,assignment]
Chromecast: type[m.DeviceEnum] = m.DeviceEnum("Chromecast", {}, module="orc")  # type: ignore[call-arg,arg-type,assignment]
BroadLink: type[m.DeviceEnum] = m.DeviceEnum("BroadLink", {}, module="orc")  # type: ignore[call-arg,arg-type,assignment]
AC: type[m.DeviceEnum] = m.DeviceEnum("AC", {}, module="orc")  # type: ignore[call-arg,arg-type,assignment]
USB: type[m.DeviceEnum] = m.DeviceEnum("USB", {}, module="orc")  # type: ignore[call-arg,arg-type,assignment]
Sensor: type[m.DeviceEnum] = m.DeviceEnum("Sensor", {}, module="orc")  # type: ignore[call-arg,arg-type,assignment]


class ConfigNotLoadedError(AttributeError):
    pass


class Config:
    def __init__(self) -> None:
        # visible as orc.config before the parse in load(): modules imported by
        # `plugin`/`provider` config lines read it at import time
        globals()["config"] = self

    def __getattr__(self, name: str) -> Any:
        raise ConfigNotLoadedError(f"orc config not loaded (reading {name!r}): the entry point must call config.load() first")

    def load(self, secrets: m.Secrets | None = None, zigbee_config: dict[Any, tuple[Any, ...]] | None = None) -> None:
        if secrets is None:
            self._load(m.Secrets(), {})
            secrets = self.providers.secrets.fetch_secrets()
            self._check_secrets(secrets)
        if zigbee_config is None:
            zigbee_config = self.providers.mqtt.discover(self.providers.adapter, secrets)
        self._load(secrets, zigbee_config)

    def _load(self, secrets: m.Secrets, zigbee_config: dict[Any, tuple[Any, ...]]) -> None:
        self.config_dir = os.getenv("ORC_CONFIG_DIR", "src")
        self.secrets = secrets
        plugins_dir = Path(self.config_dir) / "plugins"
        self.plugin_configs = {p.relative_to(plugins_dir).with_suffix("").as_posix(): p.read_text() for p in plugins_dir.glob("**/*.orc")}

        parsed = parse_config((Path(self.config_dir) / "config.orc").read_text(), zigbee_config)
        validate(parsed)
        self._install(parsed)

        self.default_config = self.routines["ROUTINE_DEFAULT"]
        self.reset_config = self.routines["ROUTINE_RESET"]
        self.schedule_routines = {e.routine.name: e.routine for theme in self.themes.values() for e in theme.entries}
        self.rooms = parsed.room

    @property
    def devices(self) -> "m.DeviceNamespace":
        return self.registry.devices

    def plugin(self, id: str) -> m.CallablePlugin | None:
        return next((p for p in of_type(self.plugins, m.CallablePlugin) if p.name == id), None)

    def plugins_in(self, section: str) -> tuple[m.Plugin, ...]:
        return tuple(p for p in self.plugins if p.section == section)

    def plugin_for(self, module: ModuleType) -> m.CallablePlugin:
        plugin = next((p for p in self.plugins if p.module is module), None)
        if plugin is None:
            raise ConfigError(f"No plugin line configured for module {module.__name__!r}")
        return plugin

    def _check_secrets(self, secrets: m.Secrets) -> None:
        if self.providers.secrets is secrets_stub:
            return
        if problems := check_secrets(secrets, self.secret_needs):
            raise ConfigError("Secrets: " + "; ".join(f"{name} {problem}" for name, problem in problems.items()))

    def _install(self, parsed: SimpleNamespace) -> None:
        self.settings = parsed.setting
        self.plugins = parsed.plugins
        declarations = collect_declarations(parsed.plugin_modules, self.plugin_configs)

        if "orc.api" in sys.modules:  # a load can run before api is imported (the blank pass, extras conftest) — and needs no dispatch
            sys.modules["orc.api"].declare_core(declarations)

        globals().update(parsed.enums)
        self.registry = declarations.build(parsed.enums)
        self.virtual_devices = parsed.virtual_devices

        self.people = parsed.person
        self.providers = parsed.provider
        self.secret_needs = secret_needs(self.registry, parsed.provider, parsed.tag)
        self.ble_tags = ble_keys(parsed.tag, self.secrets, parsed.setting.tz)
        self.routines = parsed.routine
        self.themes = parsed.theme
        self.ad_hoc_routines = parsed.ad_hoc
        self.remotes = parsed.remote
        self.button_highlights = parsed.highlight


config = Config()
