from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any, Protocol


class FeedService(Protocol):
    def fetch_ical(self, start: datetime, end: datetime | timedelta, url: str, timeout: int) -> Iterator[Any]: ...
