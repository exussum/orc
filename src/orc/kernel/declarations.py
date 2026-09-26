import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from command_cfg import ConfigError
from flask import Blueprint

from orc import plugins as core_plugins
from orc.model import DeviceEnum, DeviceNamespace, Registry


@dataclass
class Declarations:
    controllable_devices: list[str] = field(default_factory=list)
    device_icons: dict[str, str] = field(default_factory=dict)
    dispatch_handlers: dict[str, Callable[..., None]] = field(default_factory=dict)
    state_providers: dict[str, Callable[[], Any]] = field(default_factory=dict)
    setup_hooks: list[Callable[[Any], None]] = field(default_factory=list)
    scripts: dict[str, Path] = field(default_factory=dict)
    button_labels: dict[str, str] = field(default_factory=dict)
    blueprints: list[tuple[str, str, Blueprint]] = field(default_factory=list)
    _current_plugin: str = ""

    def declare_dispatch(self, name: str, fn: Callable[..., None]) -> None:
        self.dispatch_handlers[name] = fn

    def declare(
        self,
        *,
        controllable: Iterable[str] = (),
        icons: dict[str, str] | None = None,
        dispatch: dict[str, Callable[..., None]] | None = None,
        state_providers: dict[str, Callable[[], Any]] | None = None,
        setup: Iterable[Callable[[Any], None]] = (),
        scripts: Iterable[Path | str] = (),
        button_labels: dict[str, str] | None = None,
        blueprints: dict[str, Blueprint] | None = None,
    ) -> None:
        self.device_icons.update(icons or {})
        self.dispatch_handlers.update(dispatch or {})
        self.state_providers.update(state_providers or {})
        self.scripts.update({Path(s).name: Path(s) for s in scripts})
        self.button_labels.update(button_labels or {})
        self.blueprints += [(self._current_plugin, ns, bp) for ns, bp in (blueprints or {}).items()]

        for name in controllable:
            if name not in self.controllable_devices:
                self.controllable_devices.append(name)
        for hook in setup:
            if hook not in self.setup_hooks:
                self.setup_hooks.append(hook)

    def build(self, enums: dict[str, type[DeviceEnum]]) -> Registry:
        return Registry(
            devices=DeviceNamespace(**enums),
            device_icons=dict(self.device_icons),
            controllable_devices=frozenset(self.controllable_devices),
            dispatch_handlers=dict(self.dispatch_handlers),
            scripts=dict(self.scripts),
            button_labels=dict(self.button_labels),
            state_providers=dict(self.state_providers),
            setup_hooks=list(self.setup_hooks),
            blueprints=list(self.blueprints),
        )


def _check_contract(module: ModuleType) -> None:
    import orc

    banned = tuple(obj for obj in (orc, orc.config, sys.modules.get("orc.api")) if obj)
    package = [module] + [mod for name, mod in sys.modules.items() if name.startswith(module.__name__ + ".") and mod]
    offenders = [f"{mod.__name__}.{name}" for mod in package for name, val in vars(mod).items() if any(val is b for b in banned)]
    if offenders:
        raise ConfigError(
            f"Plugin {module.__name__!r} holds orc runtime globals; reach them through the AppContext: " + ", ".join(offenders)
        )


def collect_declarations(modules: Iterable[ModuleType]) -> Declarations:
    declarations = Declarations()
    seen: set[str] = set()
    for module in modules:
        if module is core_plugins or module.__name__ in seen:
            continue
        seen.add(module.__name__)
        _check_contract(module)
        declarations._current_plugin = module.__name__.split(".")[-1]
        module.declare(declarations)
    return declarations
