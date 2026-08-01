from collections.abc import Iterator
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any

import icalendar
import recurring_ical_events
import requests

from orc import config
from orc.decorators import requires_enabled
from orc.model import WeatherCondition

_SUNNY_CODES: set[int] = {0, 1}  # WMO 0=clear sky, 1=mainly clear


@requires_enabled([])
@lru_cache(maxsize=2)
def fetch_holidays(year: int) -> Any:
    result = requests.get(config.secrets.market_holidays_url, timeout=config.http_timeout).json()
    if "error" in result:
        raise RuntimeError(result["error"])
    return result


@requires_enabled(frozenset())
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


@requires_enabled(lambda *_: iter(()))
def fetch_ical(start: datetime, end: datetime | timedelta) -> Iterator[Any]:
    ical_string = requests.get(config.secrets.ics_url, timeout=config.http_ical_timeout).content
    a_calendar = icalendar.Calendar.from_ical(ical_string)
    return (e for e in recurring_ical_events.of(a_calendar).between(start, end) if type(e.start) is datetime and e.start >= start)
