from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import icalendar
import recurring_ical_events
import requests

from orc import config


def fetch_ical(start: datetime, end: datetime | timedelta) -> Iterator[Any]:
    ical_string = requests.get(config.secrets.ics_url, timeout=config.settings.http_ical_timeout).content
    a_calendar = icalendar.Calendar.from_ical(ical_string)
    return (e for e in recurring_ical_events.of(a_calendar).between(start, end) if type(e.start) is datetime and e.start >= start)
