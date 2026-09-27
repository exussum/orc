from collections.abc import Callable
from datetime import date
from typing import Any

from orc.dal import warn_stub

SECRETS: dict[str, Callable[[str], Any]] = {}

warn_stub("holiday")


def market_holiday(today: date) -> bool:
    return False
