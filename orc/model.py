from dataclasses import dataclass, KW_ONLY, replace
from enum import Enum
from datetime import time, timedelta
from typing import Optional, List


@dataclass
class ScheduledConfig:
    _: KW_ONLY
    name: Optional[str] = None
    when: Optional[str] = None
    offset: Optional[str] = timedelta()

    def __post_init__(self):
        if not isinstance(self.offset, timedelta) and self.offset:
            parts = self.offset.split(" ")
            amt = int(parts[0])
            self.offset = timedelta(**{parts[1]: amt})
        if self.when and not isinstance(self.when, time) and ":" in self.when:
            hour, minute = tuple(self.when.split(":"))
            self.when = time(int(hour), int(minute))


@dataclass
class SimpleConfig(ScheduledConfig):
    what: object
    state: object


class LightConfig(SimpleConfig):
    pass


class SoundConfig(SimpleConfig):
    pass


@dataclass
class RoutineConfig(ScheduledConfig):
    items: SimpleConfig


def scan(*configs):
    for e in configs:
        if isinstance(e, RoutineConfig):
            for w in e.items:
                w.offset = e.offset
                w.when = e.when
    return configs
