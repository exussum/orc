from orc.dal import warn_stub
from orc.model import DeviceEnum

warn_stub("blaster")


def set_ac(device: DeviceEnum, codes_file: str, mode: str, fan: str, temp: int) -> None:
    pass


def tv_toggle(device: DeviceEnum, codes_file: str) -> None:
    pass


def ac_off(device: DeviceEnum, codes_file: str) -> None:
    pass
