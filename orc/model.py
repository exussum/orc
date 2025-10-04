from dataclasses import dataclass
from datetime import time, timedelta
from typing import Optional, List


@dataclass
class SimpleConfig:
    when: str
    what: object
    state: object
    offset: Optional[str] = timedelta()

    def __post_init__(self):
        if not isinstance(self.offset, timedelta) and self.offset:
            parts = self.offset.split(" ")
            amt = int(parts[0])
            self.offset = timedelta(**{parts[1]: amt})
        if not isinstance(self.when, time) and ":" in self.when:
            hour, minute = tuple(self.when.split(":"))
            self.when = time(int(hour), int(minute))


class LightConfig(SimpleConfig): ...


class SoundConfig(SimpleConfig): ...
