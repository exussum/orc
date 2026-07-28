"""Plugin registration: ``run_registration`` builds a fresh ``RegistryBuilder`` per
config load and hands it to each plugin's ``register(core)`` hook as ``core``;
``core.register_plugin(...)`` populates it, and ``RegistryBuilder.build`` snapshots it
into an immutable ``model.Registry``. The ``RegistryBuilder`` is transient — nothing
keeps it after ``build``.
"""

import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from orc.model import DeviceType, Registry

if TYPE_CHECKING:
    from orc.model import DeviceEnum


@dataclass
class RegistryBuilder:
    """Mutable registration accumulator for one config load: plugins fill it via
    ``register_plugin`` from their ``register(core)`` hook, then ``build`` snapshots
    it into an immutable ``Registry`` and it is discarded. Core seeds its device-type
    defaults here."""

    device_types: list[str] = field(default_factory=lambda: ["Light", "Chromecast", "BroadLink", "AC"])
    controllable_devices: list[str] = field(default_factory=lambda: ["Light", "Chromecast", "AC"])
    reset_excluded_types: set[str] = field(default_factory=set)
    device_icons: dict[str, str] = field(default_factory=dict)
    state_providers: dict[str, Callable[[], Any]] = field(default_factory=dict)
    dispatch_handlers: dict[str, Callable[..., None]] = field(default_factory=dict)
    startup_hooks: list[Callable[[], None]] = field(default_factory=list)
    click_hooks: dict[str, str] = field(default_factory=dict)
    button_labels: dict[str, str] = field(default_factory=dict)
    cron_jobs: list[tuple[Callable[..., None], str, str, str]] = field(default_factory=list)

    def register_device_type(self, name: str) -> None:
        if name not in self.device_types:
            self.device_types.append(name)

    def register_dispatch(self, name: str, fn: Callable[..., None]) -> None:
        self.dispatch_handlers[name] = fn

    def register_plugin(
        self,
        *,
        device_types: Iterable[str] = (),
        controllable: Iterable[str] = (),
        reset_excluded: Iterable[str] = (),
        icons: dict[str, str] | None = None,
        dispatch: dict[str, Callable[..., None]] | None = None,
        state_providers: dict[str, Callable[[], Any]] | None = None,
        startup: Iterable[Callable[[], None]] = (),
        on_click: dict[str, str] | None = None,
        button_labels: dict[str, str] | None = None,
        crons: Iterable[tuple[Callable[..., None], str, str, str]] = (),
    ) -> None:
        """One entry point for a plugin to register everything it contributes; all
        pieces are optional."""
        self.reset_excluded_types.update(reset_excluded)
        self.device_icons.update(icons or {})
        self.dispatch_handlers.update(dispatch or {})
        self.state_providers.update(state_providers or {})
        self.click_hooks.update(on_click or {})
        self.button_labels.update(button_labels or {})
        self.cron_jobs.extend(crons)

        for name in device_types:
            self.register_device_type(name)
        for name in controllable:
            if name not in self.controllable_devices:
                self.controllable_devices.append(name)
        for hook in startup:
            if hook not in self.startup_hooks:
                self.startup_hooks.append(hook)

    def build(self, enums: "dict[str, type[DeviceEnum]]") -> Registry:
        devices = {
            name: DeviceType(
                cls=cls,
                icon=self.device_icons.get(name, "light-bulb"),
                controllable=name in self.controllable_devices,
                reset_excluded=name in self.reset_excluded_types,
                dispatch=self.dispatch_handlers.get(name),
            )
            for name, cls in enums.items()
        }
        return Registry(
            devices=devices,
            click_hooks=dict(self.click_hooks),
            button_labels=dict(self.button_labels),
            state_providers=dict(self.state_providers),
            startup_hooks=list(self.startup_hooks),
            cron_jobs=list(self.cron_jobs),
        )


def run_registration(module_paths: Iterable[str]) -> RegistryBuilder:
    """Build a fresh ``RegistryBuilder`` and let each plugin package's optional
    ``register(core)`` hook populate it, where ``core`` is that builder.

    The packages are already imported (build_plugins resolved their functions), so
    they are looked up in sys.modules rather than imported again. Packages are
    de-duped, so a package listed by several plugins registers once.
    """
    builder = RegistryBuilder()
    seen: set[str] = set()
    for path in module_paths:
        package = path.split(".")[0]
        if package == "orc" or package in seen:
            continue
        seen.add(package)
        module = sys.modules.get(package)
        register = getattr(module, "register", None) if module is not None else None
        if register is not None:
            register(builder)
    return builder
