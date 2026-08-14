from collections.abc import Iterator
from datetime import datetime, time, timedelta
from typing import Any

import icalendar


def fetch_ical(start: datetime, end: datetime | timedelta, url: str, timeout: int) -> Iterator[Any]:
    event = icalendar.Event()
    event.add("uid", "stub-event")
    event.add("summary", "Stub Event")
    event.add("dtstart", datetime.combine(start.date(), time(8), tzinfo=start.tzinfo))
    return iter([event])
