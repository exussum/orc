from datetime import datetime

from orc.dal import warn_stub
from orc.model import WeatherCondition

warn_stub("weather")


def fetch_weather(now: datetime, lat: float, lon: float) -> frozenset[WeatherCondition]:
    return frozenset({WeatherCondition.SUNNY})
