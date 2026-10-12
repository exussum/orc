from datetime import datetime
from typing import Protocol


class DriveService(Protocol):
    def drive_minutes(self, key: str, origin: str, dest: str, timeout: int) -> int: ...


class FlightService(Protocol):
    def arrival(self, key: str, iata: str, when: datetime, airport: str | None, timeout: int) -> tuple[datetime, str, str | None]: ...
