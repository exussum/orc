from collections.abc import Callable, Sequence
from typing import Any

import orc
from orc import model as m
from orc.dal import warn_stub

REQUIRED_SECRETS: dict[str, Callable[[str], Any]] = {}

namespaces: tuple[str, ...] = ()
device_types: tuple[str, ...] = ("Light", "Button", "Sensor")

warn_stub("adapter")


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


def discover(messages: Sequence[m.Message]) -> dict[str, tuple[str, frozenset[m.Capability]]]:
    return {}


def _state(device: m.DeviceEnum, attributes: dict[str, Any]) -> m.DeviceState:
    name = device.label if device.label and device.label != device.name else device.name.lower().replace("_", " ")
    return m.DeviceState(m.Device(device.value, name, StubSource.STUB), attributes, "2026-01-01 08:00:00")


def _light_attributes(n: int) -> dict[str, Any]:
    on = n % 3 == 0
    return {"switch": m.ON if on else m.OFF, "level": 70 if on else 100}


def _sensor_attributes(name: str) -> dict[str, Any]:
    if "TEMP" in name:
        return {"temperature": 72.5, "humidity": 45}
    elif "DOOR" in name or "CONTACT" in name:
        return {"contact": "closed", "battery": 100}
    return {"motion": "inactive", "battery": 98}
