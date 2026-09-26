import threading
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any


class LockedDict[K, V]:
    def __init__(self, initial: Mapping[K, V] | None = None) -> None:
        self._lock = threading.Lock()
        self._data: dict[K, V] = dict(initial) if initial else {}

    def __setitem__(self, key: K, value: V) -> None:
        with self._lock:
            self._data[key] = value

    def get(self, key: K, default: V | None = None) -> V | None:
        with self._lock:
            return self._data.get(key, default)

    def pop(self, key: K, default: V | None = None) -> V | None:
        with self._lock:
            return self._data.pop(key, default)

    def update(self, key: K, fn: Callable[[V | None], V | None]) -> V | None:
        with self._lock:
            new = fn(self._data.get(key))
            if new is None:
                return None
            self._data[key] = new
            return new

    def values(self) -> list[V]:
        with self._lock:
            return list(self._data.values())

    def copy(self) -> dict[K, V]:
        with self._lock:
            return dict(self._data)


class LockedDeque[T]:
    def __init__(self, maxlen: int) -> None:
        self._lock = threading.Lock()
        self._data: deque[T] = deque(maxlen=maxlen)

    def append(self, item: T) -> None:
        with self._lock:
            self._data.append(item)

    def appendleft(self, item: T) -> None:
        with self._lock:
            self._data.appendleft(item)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def snapshot(self) -> list[T]:
        with self._lock:
            return list(self._data)


def where[V](items: Mapping[str, V], **kwargs: Any) -> dict[str, V]:
    return {k: v for k, v in items.items() if all(getattr(v, attr) == val for attr, val in kwargs.items())}
