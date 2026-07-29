import dataclasses
from typing import Any

from orc import model as m
from orc.collections import LockedDict


@dataclasses.dataclass(frozen=True)
class SensorState:
    name: str
    device_id: int
    battery: m.BatteryLevel | None = None
    attributes: dict[str, Any] = dataclasses.field(default_factory=dict)
    last_activity: str | None = None


_states: LockedDict[int, SensorState] = LockedDict()  # device id -> last recorded state


def record(device: Any) -> None:
    """Store a device document (an ``orc.model.DeviceState``) delivered by the
    central MQTT listener; the retained flood at connect provides the initial state."""
    battery = device.attributes.get("battery")
    _states[device.id] = SensorState(
        name=device.name,
        device_id=device.id,
        battery=m.BatteryLevel.from_fraction(battery, 100) if battery is not None else None,
        attributes=device.attributes,
        last_activity=device.last_activity,
    )


def get(device_id: int) -> SensorState | None:
    return _states.get(device_id)
