from collections.abc import Callable
from typing import Any

from orc import model as m
from orc.dal import warn_stub

REQUIRED_SECRETS: dict[str, Callable[[str], Any]] = {}

warn_stub("findhub")


def identity_key(device: str) -> m.BleKey:
    return m.BleKey(device.encode().ljust(32, b"\0")[:32], 0)
