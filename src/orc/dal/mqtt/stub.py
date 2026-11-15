import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import orc
from orc import model as m
from orc.dal import warn_stub

REQUIRED_SECRETS: dict[str, Callable[[str], Any]] = {}

namespaces: tuple[str, ...] = ()
device_types: tuple[str, ...] = ("Light", "Button", "Sensor")

warn_stub("adapter")

_DIMMABLE = frozenset({m.Capability.change_level, m.Capability.color})


def _capabilities(n: int) -> frozenset[m.Capability]:
    return frozenset() if n % 3 == 2 else _DIMMABLE


class StubSource(m.SourceEnum):
    STUB = "stub"


def attach(publish: Callable[[m.Message], None]) -> None:
    pass


def decode(topic: str, doc: dict[str, Any]) -> tuple[m.Status, ...]:
    return ()


def encode(device: m.DeviceEnum, command: Any) -> tuple[m.Message, ...]:
    return ()


def snapshot() -> tuple[m.DeviceState, ...]:
    lights = getattr(orc.config.devices, "Light", ())
    sensors = getattr(orc.config.devices, "Sensor", ())
    states = [_state(light, _light_attributes(n)) for n, light in enumerate(lights)]
    states += [_state(sensor, _sensor_attributes(sensor.name)) for sensor in sensors]
    return tuple(states)


def start() -> None:
    pass


class _EveryLight(dict[str, Any]):
    def __bool__(self) -> bool:
        return True

    def get(self, key: str, default: Any = None) -> tuple[str, frozenset[m.Capability]]:
        return key, _DIMMABLE


def discover(messages: Sequence[m.Message]) -> dict[str, tuple[str, frozenset[m.Capability]]]:
    from orc.kernel.loader import parse_config

    text = (Path(os.environ["ORC_CONFIG_DIR"]) / "config.orc").read_text()
    lights = parse_config(text, _EveryLight()).enums.get("Light", ())
    return {light.value: (light.value, _capabilities(n)) for n, light in enumerate(lights)}


def _state(device: m.DeviceEnum, attributes: dict[str, Any]) -> m.DeviceState:
    name = device.label if device.label and device.label != device.name else device.name.lower().replace("_", " ")
    return m.DeviceState(m.Device(device.value, name, StubSource.STUB), attributes, "2026-01-01 08:00:00")


def _light_attributes(n: int) -> dict[str, Any]:
    on = n % 3 == 0
    attributes: dict[str, Any] = {"switch": m.ON if on else m.OFF}
    if _capabilities(n):
        attributes["level"] = 70 if on else 100
    return attributes


def _sensor_attributes(name: str) -> dict[str, Any]:
    if "TEMP" in name:
        return {"temperature": 72.5, "humidity": 45}
    elif "DOOR" in name or "CONTACT" in name:
        return {"contact": "closed", "battery": 100}
    return {"motion": "inactive", "battery": 98}
