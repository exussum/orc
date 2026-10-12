# Separate from api.py to avoid a circular import: api.py imports these decorators,
# so anything that imports api must not live here.

import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import update_wrapper, wraps
from typing import Any, Concatenate, overload

from sqlalchemy.engine.url import make_url

import orc

audio_lock = threading.Lock()


class Mappable[**P, R]:
    """Let a caller hand the result straight to a shape it wants: f(mapper=dict)."""

    def __init__(self, f: Callable[P, R]) -> None:
        self.f = f
        update_wrapper(self, f)

    @overload
    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R: ...
    @overload
    def __call__(self, *args: Any, mapper: Callable[[R], Any], **kwargs: Any) -> Any: ...
    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        mapper = kwargs.pop("mapper", None)
        result = self.f(*args, **kwargs)
        return result if mapper is None else mapper(result)


def mappable[**P, R](f: Callable[P, R]) -> Mappable[P, R]:
    return Mappable(f)


def requires_ctx[**P, R](f: Callable[P, R]) -> Callable[P, R]:
    @wraps(f)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        if kwargs.get("ctx") is None:
            raise ValueError("ctx must be injected by the executor")
        return f(*args, **kwargs)

    return wrapper


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    db_path = make_url(orc.config.settings.jobs_db).database
    assert db_path is not None
    conn = sqlite3.connect(db_path)
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
