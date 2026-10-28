from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import icalendar


def fetch_ical(start: datetime, end: datetime | timedelta, url: str, timeout: int) -> Iterator[Any]:
    meeting = icalendar.Event()
    meeting.add("uid", "stub-meeting")
    meeting.add("summary", "Stub Meeting")
    meeting.add("dtstart", start + timedelta(hours=1))
    meeting.add("dtend", start + timedelta(hours=2))
    meeting.add("x-google-conference", "https://meet.google.com/abc-defg-hij")
    visit = icalendar.Event()
    visit.add("uid", "stub-visit")
    visit.add("summary", "Stub Visit")
    visit.add("dtstart", start + timedelta(hours=3))
    visit.add("dtend", start + timedelta(hours=4))
    visit.add("location", "1 Main St, Springfield, IL 62701")
    return iter([meeting, visit])
