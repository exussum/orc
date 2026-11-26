from collections.abc import Callable
from datetime import datetime
from functools import lru_cache
from typing import Any, NamedTuple

import requests

import orc
from orc import model as m

REQUIRED_SECRETS: dict[str, Callable[[str], Any]] = {}

_SUNNY_CODES: set[int] = {0, 1}  # WMO 0=clear sky, 1=mainly clear


class Hour(NamedTuple):
    code: int
    temperature: float


def fetch_weather(now: datetime, lat: float, lon: float) -> frozenset[m.WeatherCondition]:
    code = _hour_forecast(now, lat, lon).code
    return frozenset({m.WeatherCondition.SUNNY if code in _SUNNY_CODES else m.WeatherCondition.CLOUDY})


def fetch_temperature(now: datetime, lat: float, lon: float) -> float:
    return _hour_forecast(now, lat, lon).temperature


def _hour_forecast(when: datetime, lat: float, lon: float) -> Hour:
    fetched = datetime.now(orc.config.settings.tz).replace(minute=0, second=0, microsecond=0)
    return _forecast(when.replace(minute=0, second=0, microsecond=0), fetched, lat, lon)


@lru_cache(maxsize=10)
def _forecast(now: datetime, fetched: datetime, lat: float, lon: float) -> Hour:
    date_str = now.strftime("%Y-%m-%d")
    response = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": str(lat),
            "longitude": str(lon),
            "hourly": "weather_code,temperature_2m",
            "temperature_unit": "fahrenheit",
            "start_date": date_str,
            "end_date": date_str,
            "timezone": str(orc.config.settings.tz),
        },
        timeout=orc.config.settings.http_timeout,
    )
    response.raise_for_status()
    hourly = response.json()["hourly"]
    return Hour(hourly["weather_code"][now.hour], hourly["temperature_2m"][now.hour])
