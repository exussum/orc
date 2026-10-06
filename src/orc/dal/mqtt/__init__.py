from typing import Any

from orc import model as m


def switch_on(light: m.DeviceEnum, value: Any) -> bool:
    if isinstance(value, int) and value not in (0, 100):
        raise ValueError(f"{light.name} does not support ChangeLevel; cannot set brightness {value}")
    return value == m.ON or value == 100
