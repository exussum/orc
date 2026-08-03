import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from orc.collections import doc_to_sub_tables
from orc.model import DeviceType, Registry, column_to_value

if TYPE_CHECKING:
    from orc.model import DeviceEnum


@dataclass
class Declarations:
    plugin_docs: dict[str, Any] = field(default_factory=dict)
    device_types: list[str] = field(default_factory=lambda: ["Light", "Chromecast", "BroadLink", "AC", "Button"])
    controllable_devices: list[str] = field(default_factory=lambda: ["Light", "Chromecast", "AC"])
    reset_excluded_types: set[str] = field(default_factory=set)
    device_icons: dict[str, str] = field(default_factory=dict)
    dispatch_handlers: dict[str, Callable[..., None]] = field(default_factory=dict)
    state_providers: dict[str, Callable[[], Any]] = field(default_factory=dict)
    setup_hooks: list[Callable[[Any], None]] = field(default_factory=list)
    click_hooks: dict[str, str] = field(default_factory=dict)
    button_labels: dict[str, str] = field(default_factory=dict)

    def declare_device_type(self, name: str) -> None:
        if name not in self.device_types:
            self.device_types.append(name)

    def declare_dispatch(self, name: str, fn: Callable[..., None]) -> None:
        self.dispatch_handlers[name] = fn

    def load_plugin_config(self, name: str, schema: dict[str, tuple[str, ...]]) -> SimpleNamespace:
        return load_plugin_config(name, self.plugin_docs, schema)

    def declare(
        self,
        *,
        device_types: Iterable[str] = (),
        controllable: Iterable[str] = (),
        reset_excluded: Iterable[str] = (),
        icons: dict[str, str] | None = None,
        dispatch: dict[str, Callable[..., None]] | None = None,
        state_providers: dict[str, Callable[[], Any]] | None = None,
        setup: Iterable[Callable[[Any], None]] = (),
        on_click: dict[str, str] | None = None,
        button_labels: dict[str, str] | None = None,
    ) -> None:
        self.reset_excluded_types.update(reset_excluded)
        self.device_icons.update(icons or {})
        self.dispatch_handlers.update(dispatch or {})
        self.state_providers.update(state_providers or {})
        self.click_hooks.update(on_click or {})
        self.button_labels.update(button_labels or {})

        for name in device_types:
            self.declare_device_type(name)
        for name in controllable:
            if name not in self.controllable_devices:
                self.controllable_devices.append(name)
        for hook in setup:
            if hook not in self.setup_hooks:
                self.setup_hooks.append(hook)

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
            setup_hooks=list(self.setup_hooks),
        )


def collect_declarations(module_paths: Iterable[str], plugin_docs: dict[str, Any]) -> Declarations:
    declarations = Declarations(plugin_docs=plugin_docs)
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


def load_plugin_config(name: str, plugin_docs: dict[str, Any], schema: dict[str, tuple[str, ...]]) -> SimpleNamespace:
    doc = plugin_docs.get(name)
    if doc is None:
        raise FileNotFoundError(f"no config doc 'plugins/{name}.md'")

    attrs: dict[str, Any] = {}
    for section, columns in schema.items():
        if len(columns) == 2:
            col_attr = columns[1].lower()
            for trigger, rows in doc_to_sub_tables(doc, section, columns, cast=column_to_value):
                attrs[trigger] = getattr(rows[0], col_attr)
        else:
            attrs[section.lower()] = SimpleNamespace(
                **{trigger: rows for trigger, rows in doc_to_sub_tables(doc, section, columns, cast=column_to_value)}
            )

    return SimpleNamespace(**attrs)
