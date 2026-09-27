from collections.abc import Callable
from typing import Any

from orc.dal import warn_stub
from orc.model import DeviceEnum

SECRETS: dict[str, Callable[[str], Any]] = {}

warn_stub("blaster")


def tv_toggle(device: DeviceEnum, codes_file: str) -> None:
    pass
