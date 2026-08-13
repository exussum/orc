# Separate from api.py to avoid a circular import: api.py imports these decorators,
# so anything that imports api must not live here.

import contextlib
import os
import threading
from collections.abc import Callable, Iterator
from functools import wraps
from typing import Any

from orc import model as m

audio_lock = threading.Lock()


@contextlib.contextmanager
def silence_fd(fd: int) -> Iterator[None]:
    saved = os.dup(fd)
    with open(os.devnull, "w") as devnull:
        os.dup2(devnull.fileno(), fd)
        try:
            yield
        finally:
            os.dup2(saved, fd)
            os.close(saved)


def requires_ctx[**P, R](f: Callable[P, R]) -> Callable[P, R]:
    @wraps(f)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        if kwargs.get("ctx") is None:
            raise ValueError("ctx must be injected by the executor")
        return f(*args, **kwargs)

    return wrapper


def synchronized[R](method: Callable[..., R]) -> Callable[..., R]:
    @wraps(method)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> R:
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


def unwrap_rule_container[R](f: Callable[..., R]) -> Callable[..., None]:
    def wrapper(*args: Any, **kwargs: Any) -> None:
        if isinstance(args[0], m.Routine | m.Configs):
            for e in args[0].items:
                f(e, *args[1:], **kwargs)
        elif len(args) > 1 and isinstance(args[1], m.Routine | m.Configs):
            for e in args[1].items:
                f(args[0], e, *args[2:], **kwargs)
        else:
            f(*args, **kwargs)

    return wrapper
