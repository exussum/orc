import sys

from orc import model as m

_spoken: list[str] = []
_alerted: list[str] = []
_volumes: dict[m.DeviceEnum, int] = {}


def _warn(action: str, device: m.DeviceEnum) -> None:
    print(f"warning: orc.dal.audio.stub is active — {action} on {device.name} did nothing", file=sys.stderr)


def speak(device: m.DeviceEnum, text: str) -> None:
    _warn("speak", device)
    _spoken.append(text)


def alert(device: m.DeviceEnum, path: str) -> None:
    _warn("alert", device)
    _alerted.append(path)


def set_volume(device: m.DeviceEnum, lvl: int) -> None:
    _warn("set_volume", device)
    _volumes[device] = lvl


def reset() -> None:
    _spoken.clear()
    _alerted.clear()
    _volumes.clear()
