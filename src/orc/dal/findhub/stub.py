from collections.abc import Callable
from typing import Any

from orc import model as m
from orc.dal import warn_stub

REQUIRED_SECRETS: dict[str, Callable[[str], Any]] = {}

warn_stub("findhub")


def identity_key(device: str, secrets: m.Secrets, timeout: int) -> m.TagKey:
    return m.TagKey(device.encode().ljust(32, b"\0")[:32], 0)
