from collections.abc import Callable, Sequence
from typing import Any

from orc_engine import model as em

from orc import model as m
from orc.dal import warn_stub
from orc.dal.mqtt import switch_command

REQUIRED_SECRETS: dict[str, Callable[[str], Any]] = {}

warn_stub("mqtt")

_states: dict[m.DeviceEnum, Any] = {}
_listeners: list[m.Listener] = []
_external_listeners: list[m.Listener] = []


def start() -> None:
    pass


def fetch_hubitat_config(secrets: m.Secrets, timeout: float = 3.0) -> dict[str, tuple[str, frozenset[m.Capability]]]:
    return {}


def fetch_light_states(lights: Sequence[m.DeviceEnum]) -> m.Commands:
    return tuple(em.Command(m.Devices(light), _states.get(light, m.OFF)) for light in lights)


def publish_light(light: m.DeviceEnum, on: bool | None = None, brightness: int | None = None) -> None:
    if brightness is not None and m.Capability.change_level in light.capabilities:
        _states[light] = brightness or m.OFF
        return
    _states[light] = switch_command(light, on, brightness)


def snapshot() -> list[m.DeviceState]:
    return []


def add_listener(fn: m.Listener) -> None:
    _listeners.append(fn)


def add_external_listener(fn: m.Listener) -> None:
    _external_listeners.append(fn)


def reset() -> None:
    _states.clear()
    _listeners.clear()
    _external_listeners.clear()
