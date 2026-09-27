from collections.abc import Callable
from typing import Any

from orc import model as m
from orc.dal import warn_stub

REQUIRED_SECRETS: dict[str, Callable[[str], Any]] = {}

warn_stub("hubitat")


def reboot() -> None:
    pass


def fetch_retry_stats() -> tuple[m.RetryStats, ...]:
    return ()
