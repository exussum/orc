# Separate from api.py to avoid a circular import: api.py imports these decorators
# through orc.dal.sqlite, so anything that imports api must not live here.

import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from typing import Any, Concatenate

import orc

audio_lock = threading.Lock()


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(orc.config.settings.jobs_db)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def connected[**P, R](fn: Callable[Concatenate[Any, P], R]) -> Callable[P, R]:
    @wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with connection() as conn:
            return fn(conn, *args, **kwargs)

    return wrapper
