from datetime import datetime
from typing import Protocol

from orc_plugins.travel.dal.sqlite import Connection


class DriveService(Protocol):
    def drive_minutes(self, connection: Connection, key: str, origin: str, dest: str, timeout: int) -> int: ...
    def geocode(self, connection: Connection, key: str, address: str, timeout: int) -> bool: ...


class FlightService(Protocol):
    def arrival(self, key: str, iata: str, when: datetime, airport: str | None, timeout: int) -> tuple[datetime, str, str | None]: ...
