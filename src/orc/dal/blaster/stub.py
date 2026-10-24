from collections.abc import Callable
from typing import Any

from orc import model as m
from orc.dal import warn_stub

REQUIRED_SECRETS: dict[str, Callable[[str], Any]] = {}

warn_stub("blaster")


def tv_toggle(device: m.DeviceEnum, codes_file: str) -> None:
    pass
