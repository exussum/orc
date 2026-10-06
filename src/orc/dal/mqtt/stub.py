from collections.abc import Callable
from typing import Any

from orc import model as m
from orc.dal import warn_stub
from orc.dal.mqtt import switch_on

REQUIRED_SECRETS: dict[str, Callable[[str], Any]] = {}

warn_stub("mqtt")

_states: dict[m.DeviceEnum, Any] = {}
_listeners: list[m.Listener] = []
_external_listeners: list[m.Listener] = []


def start() -> None:
    pass


def fetch_hubitat_config(secrets: m.Secrets, timeout: float = 3.0) -> dict[str, tuple[str, frozenset[m.Capability]]]:
    return {}


def register(codec: Any) -> None:
    pass


def command(device: m.DeviceEnum, value: Any) -> None:
    if isinstance(value, int) and m.Capability.change_level in device.capabilities:
        _states[device] = value or m.OFF
        return
    _states[device] = m.ON if switch_on(device, value) else m.OFF


def snapshot() -> list[m.DeviceState]:
    return [m.DeviceState(m.Device(light.value, light.name, "stub"), _attributes(state), None) for light, state in _states.items()]


def _attributes(state: Any) -> dict[str, Any]:
    return {"switch": m.ON, "level": str(state)} if isinstance(state, int) else {"switch": state}


def add_listener(fn: m.Listener) -> None:
    _listeners.append(fn)


def add_external_listener(fn: m.Listener) -> None:
    _external_listeners.append(fn)


def reset() -> None:
    _states.clear()
    _listeners.clear()
    _external_listeners.clear()
