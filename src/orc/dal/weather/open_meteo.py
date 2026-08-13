from datetime import datetime
from functools import lru_cache

import requests

from orc import config
from orc.model import WeatherCondition

_SUNNY_CODES: set[int] = {0, 1}  # WMO 0=clear sky, 1=mainly clear


def fetch_weather(now: datetime, lat: float, lon: float) -> frozenset[WeatherCondition]:
    return _fetch_weather(now.replace(minute=0, second=0, microsecond=0), lat, lon)


@lru_cache(maxsize=10)
def _fetch_weather(now: datetime, lat: float, lon: float) -> frozenset[WeatherCondition]:
    date_str = now.strftime("%Y-%m-%d")
    response = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": str(lat),
            "longitude": str(lon),
            "hourly": "weather_code",
            "start_date": date_str,
            "end_date": date_str,
            "timezone": str(config.tz),
        },
        timeout=config.http_timeout,
    )
    response.raise_for_status()
    code = response.json()["hourly"]["weather_code"][now.hour]
    return frozenset({WeatherCondition.SUNNY if code in _SUNNY_CODES else WeatherCondition.CLOUDY})
