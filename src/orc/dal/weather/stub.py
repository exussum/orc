from datetime import datetime

from orc.model import WeatherCondition


def fetch_weather(now: datetime, lat: float, lon: float) -> frozenset[WeatherCondition]:
    return frozenset({WeatherCondition.SUNNY})
