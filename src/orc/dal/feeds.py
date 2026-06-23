import sys
from datetime import datetime

import icalendar
import recurring_ical_events
import requests

from orc import config
from orc._locked_dict import LockedDict
from orc.dal._decorators import requires_enabled
from orc.model import WeatherCondition

_SUNNY_CODES = {0, 1}  # WMO 0=clear sky, 1=mainly clear

_holidays_cache = LockedDict()


@requires_enabled([])
def fetch_holidays(year):
    def fetch():
        result = requests.get(config.secrets.market_holidays_url, timeout=config.http_timeout).json()
        if "error" in result:
            print(result["error"], file=sys.stderr)
            return []
        return result

    return _holidays_cache.get_or_set(year, fetch)


@requires_enabled(frozenset())
def fetch_weather(lat, lon):
    response = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={"latitude": lat, "longitude": lon, "current": "weather_code"},
        timeout=config.http_timeout,
    )
    response.raise_for_status()
    code = response.json()["current"]["weather_code"]
    return frozenset({WeatherCondition.SUNNY} if code in _SUNNY_CODES else [])


@requires_enabled(lambda *_: iter(()))
def fetch_ical(start, end):
    ical_string = requests.get(config.secrets.ics_url, timeout=config.http_ical_timeout).content
    a_calendar = icalendar.Calendar.from_ical(ical_string)
    return (e for e in recurring_ical_events.of(a_calendar).between(start, end) if type(e.start) is datetime and e.start >= start)
