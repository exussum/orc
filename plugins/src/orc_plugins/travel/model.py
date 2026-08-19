import dataclasses
from datetime import datetime
from typing import NamedTuple

from orc_plugins.travel.dal.interfaces import DriveService, FlightService

from orc import model as m


class Log(m.LogSourceEnum):
    TRAVEL = "travel"


class Settings(NamedTuple):
    drive_backend: str
    flight_backend: str
    cron: str
    window_hours: int
    tomtom_secret: str
    aerodatabox_secret: str
    http_timeout: int
    buffer_minutes: int = 10


class Extra(NamedTuple):
    name: str
    minutes: int


class Place(NamedTuple):
    name: str
    address: str


class Runtime(NamedTuple):
    drive: DriveService
    flight: FlightService
    settings: Settings
    extras: list[Extra]
    places: list[Place]
    origin: str
    tomtom_key: str
    aerodatabox_key: str


class Arrival(NamedTuple):
    when: datetime
    where: str
    terminal: str | None


class Schedule(NamedTuple):
    leave_at: datetime | None
    next_fire: datetime | None  # None once it is time to leave
    late: bool = False
    eta: datetime | None = None  # actual arrival if leaving right now; only set when late


class Submission(NamedTuple):
    destination: str | None
    arrive: datetime | None
    flight: str | None
    extras: list[str]
    place: str | None = None


@dataclasses.dataclass
class TravelJob:
    summary: str
    destination: str
    arrive: datetime
    extras: set[str]
    iata: str | None = None
    airport: str | None = None
    leave_at: datetime | None = None
    place: str | None = None
    late: bool = False
    eta: datetime | None = None

    @classmethod
    def from_submission(cls, sub: Submission) -> "TravelJob":
        if sub.arrive is None:
            raise ValueError("arrive is required")
        if sub.flight:
            return cls(sub.flight, "", sub.arrive, set(sub.extras), iata=sub.flight, airport=sub.destination or None)
        if not sub.destination:
            raise ValueError("destination is required when no flight is given")
        return cls(sub.destination, sub.destination, sub.arrive, set(sub.extras), place=sub.place)
