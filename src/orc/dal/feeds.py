import sys
from datetime import datetime
from functools import lru_cache

import icalendar
import recurring_ical_events
import requests

from orc import config
from orc.dal import requires_enabled


@requires_enabled([])
@lru_cache(maxsize=2)
def fetch_holidays(year):
    result = requests.get(config.secrets.market_holidays_url, timeout=config.http_timeout).json()
    if "error" in result:
        print(result["error"], file=sys.stderr)
        return []
    return result


@requires_enabled(lambda *_: iter(()))
def fetch_ical(start, end):
    ical_string = requests.get(config.secrets.ics_url, timeout=config.http_ical_timeout).content
    a_calendar = icalendar.Calendar.from_ical(ical_string)
    return (e for e in recurring_ical_events.of(a_calendar).between(start, end) if type(e.start) is datetime and e.start >= start)
