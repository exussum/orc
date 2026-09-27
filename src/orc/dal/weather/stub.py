from collections.abc import Callable
from datetime import datetime
from typing import Any

from orc.dal import warn_stub
from orc.model import WeatherCondition

SECRETS: dict[str, Callable[[str], Any]] = {}

warn_stub("weather")


def fetch_weather(now: datetime, lat: float, lon: float) -> frozenset[WeatherCondition]:
    return frozenset({WeatherCondition.SUNNY})
