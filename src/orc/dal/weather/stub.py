from collections.abc import Callable
from datetime import datetime
from typing import Any

from orc import model as m
from orc.dal import warn_stub

REQUIRED_SECRETS: dict[str, Callable[[str], Any]] = {}

warn_stub("weather")


def fetch_weather(now: datetime, lat: float, lon: float) -> frozenset[m.WeatherCondition]:
    return frozenset({m.WeatherCondition.SUNNY})


def fetch_temperature(now: datetime, lat: float, lon: float) -> float:
    return 70.0
