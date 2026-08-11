import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orc.model import _CLASS_SORT, DeviceEnum, DeviceType, Registry


@dataclass
class Declarations:
    controllable_devices: list[str] = field(default_factory=lambda: ["Light", "Chromecast", "AC"])
    device_icons: dict[str, str] = field(default_factory=dict)
    dispatch_handlers: dict[str, Callable[..., None]] = field(default_factory=dict)
    state_providers: dict[str, Callable[[], Any]] = field(default_factory=dict)
    setup_hooks: list[Callable[[Any], None]] = field(default_factory=list)
    scripts: dict[str, Path] = field(default_factory=dict)
    button_labels: dict[str, str] = field(default_factory=dict)

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
    ) -> None:
        self.device_icons.update(icons or {})
        self.dispatch_handlers.update(dispatch or {})
        self.state_providers.update(state_providers or {})
        self.scripts.update({Path(s).name: Path(s) for s in scripts})
        self.button_labels.update(button_labels or {})

        for name in controllable:
            if name not in self.controllable_devices:
                self.controllable_devices.append(name)
                _CLASS_SORT.setdefault(name, len(_CLASS_SORT))
        for hook in setup:
            if hook not in self.setup_hooks:
                self.setup_hooks.append(hook)

    def build(self, enums: dict[str, type[DeviceEnum]]) -> Registry:
        devices = {
            name: DeviceType(
                cls=cls,
                icon=self.device_icons.get(name, "light-bulb"),
                controllable=name in self.controllable_devices,
                dispatch=self.dispatch_handlers.get(name),
            )
            for name, cls in enums.items()
        }
        return Registry(
            devices=devices,
            scripts=dict(self.scripts),
            button_labels=dict(self.button_labels),
            state_providers=dict(self.state_providers),
            setup_hooks=list(self.setup_hooks),
        )


def collect_declarations(module_paths: Iterable[str]) -> Declarations:
    declarations = Declarations()
    seen: set[str] = set()
    for path in module_paths:
        package = path.split(".")[0]
        if package == "orc" or package in seen:
            continue
        seen.add(package)
        module = sys.modules.get(package)
        declare = getattr(module, "declare", None) if module is not None else None
        if declare is not None:
            declare(declarations)
    return declarations
