from orc import model as m


def switch_command(light: m.DeviceEnum, on: bool | None, brightness: int | None) -> str:
    if brightness == 0:
        on = False
    elif brightness == 100:
        on = True
    elif brightness is not None:
        raise ValueError(f"{light.name} does not support ChangeLevel; cannot set brightness {brightness}")
    return m.ON if on else m.OFF
