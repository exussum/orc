from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import icalendar
import recurring_ical_events
import requests


def fetch_ical(start: datetime, end: datetime | timedelta, url: str, timeout: int) -> Iterator[Any]:
    ical_string = requests.get(url, timeout=timeout).content
    a_calendar = icalendar.Calendar.from_ical(ical_string)
    for event in recurring_ical_events.of(a_calendar).between(start, end):
        if type(event.start) is not datetime:
            continue
        if event.start.tzinfo is None:
            event.start, event.end = event.start.replace(tzinfo=start.tzinfo), event.end.replace(tzinfo=start.tzinfo)
        if event.start >= start:
            yield event
