from collections.abc import Callable
from typing import Any

from orc import model as m
from orc.dal import warn_stub

REQUIRED_SECRETS: dict[str, Callable[[str], Any]] = {}

warn_stub("audio")

_spoken: list[str] = []
_alerted: list[str] = []
_volumes: dict[m.DeviceEnum, int] = {}


def speak(device: m.DeviceEnum, text: str) -> None:
    _spoken.append(text)


def alert(device: m.DeviceEnum, path: str) -> None:
    _alerted.append(path)


def set_volume(device: m.DeviceEnum, lvl: int) -> None:
    _volumes[device] = lvl


def fetch_state(device: m.DeviceEnum) -> m.SoundState:
    return m.SoundState(what=device, content=None, volume=_volumes.get(device, 0))


def reset() -> None:
    _spoken.clear()
    _alerted.clear()
    _volumes.clear()
