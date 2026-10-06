import time
from typing import Any

from orc.collections import LockedDeque

_MAX = 1000  # ring buffer of the most recent wire frames


class Capture:
    def __init__(self) -> None:
        self._buffer: LockedDeque[dict[str, object]] = LockedDeque(maxlen=_MAX)

    def record(self, topic: str, doc: dict[str, Any]) -> None:
        self._buffer.append({"ts": time.time(), "topic": topic, "payload": doc})

    def dump(self) -> list[dict[str, object]]:
        return self._buffer.snapshot()
