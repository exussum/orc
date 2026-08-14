from collections.abc import Sequence
from typing import Any

from orc import model as m

_states: dict[m.DeviceEnum, Any] = {}
_listeners: list[m.Listener] = []
_button_listeners: list[m.ButtonListener] = []
_external_listeners: list[m.Listener] = []


def start() -> None:
    pass


def fetch_hubitat_config(secrets: m.Secrets, timeout: float = 3.0) -> dict[str, tuple[int, frozenset[m.Capability]]]:
    return {}


def fetch_light_states(lights: Sequence[m.DeviceEnum]) -> m.Configs:
    return m.Configs(*(m.Config(what=light, state=_states.get(light, m.OFF)) for light in lights))


def publish_light(light: m.DeviceEnum, on: bool | None = None, brightness: int | None = None) -> None:
    if brightness is not None and m.Capability.change_level in light.capabilities:
        _states[light] = brightness or m.OFF
        return
    if brightness == 0:
        on = False
    elif brightness == 100:
        on = True
    elif brightness is not None:
        raise ValueError(f"{light.name} does not support ChangeLevel; cannot set brightness {brightness}")
    _states[light] = m.ON if on else m.OFF


def snapshot() -> list[m.DeviceState]:
    return []


def add_listener(fn: m.Listener) -> None:
    _listeners.append(fn)


def add_button_listener(fn: m.ButtonListener) -> None:
    _button_listeners.append(fn)


def add_external_listener(fn: m.Listener) -> None:
    _external_listeners.append(fn)


def press_button(device_id: int, button: int, event: str) -> None:
    for fn in _button_listeners:
        fn(device_id, button, event)


def reset() -> None:
    _states.clear()
    _listeners.clear()
    _button_listeners.clear()
    _external_listeners.clear()
