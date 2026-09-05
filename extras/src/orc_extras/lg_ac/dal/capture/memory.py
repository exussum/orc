from __future__ import annotations

import threading
import time
from collections import deque

_MAX = 1000  # ring buffer of the most recent wire frames

_lock = threading.Lock()
_buffer: deque[dict[str, object]] = deque(maxlen=_MAX)


def record(topic: str, payload: bytes) -> None:
    with _lock:
        _buffer.append({"ts": time.time(), "topic": topic, "payload": payload.hex()})


def dump() -> list[dict[str, object]]:
    with _lock:
        return list(_buffer)
