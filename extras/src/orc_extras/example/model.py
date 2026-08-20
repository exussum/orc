import dataclasses
from datetime import datetime
from typing import NamedTuple

from orc_extras.example.dal.interfaces import BarService, FooService

from orc import model as m


class Log(m.LogSourceEnum):
    EXAMPLE = "example"


class Settings(NamedTuple):
    foo_backend: str
    bar_backend: str
    cron: str
    window_hours: int
    foo_secret: str
    bar_secret: str
    http_timeout: int


class Widget(NamedTuple):
    name: str
    value: int


class Zone(NamedTuple):
    name: str
    value: str


class Runtime(NamedTuple):
    foo: FooService
    bar: BarService
    settings: Settings
    widgets: list[Widget]
    zones: list[Zone]
    foo_key: str
    bar_key: str


class Plan(NamedTuple):
    when: datetime
    detail: str


class Submission(NamedTuple):
    target: str | None
    when: datetime | None


@dataclasses.dataclass
class ExampleJob:
    summary: str
    target: str
    when: datetime

    @classmethod
    def from_submission(cls, sub: Submission) -> "ExampleJob":
        raise NotImplementedError
