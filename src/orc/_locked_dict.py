import threading


class LockedDict:
    def __init__(self, initial=None):
        self._lock = threading.Lock()
        self._data = dict(initial) if initial else {}

    def __contains__(self, key):
        with self._lock:
            return key in self._data

    def __getitem__(self, key):
        with self._lock:
            return self._data[key]

    def __setitem__(self, key, value):
        with self._lock:
            self._data[key] = value

    def get(self, key, default=None):
        with self._lock:
            return self._data.get(key, default)

    def get_or_set(self, key, factory):
        with self._lock:
            if key in self._data:
                return self._data[key]
            value = factory()
            self._data[key] = value
            return value

    def update(self, key, fn):
        with self._lock:
            new = fn(self._data.get(key))
            if new is None:
                return None
            self._data[key] = new
            return new

    def copy(self):
        with self._lock:
            return dict(self._data)
