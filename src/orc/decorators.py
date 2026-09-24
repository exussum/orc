# Separate from api.py to avoid a circular import: api.py imports these decorators,
# so anything that imports api must not live here.

import threading
from collections.abc import Callable
from functools import wraps
from typing import Any, Protocol, cast, overload

audio_lock = threading.Lock()


class Mappable[**P, R](Protocol):
    @overload
    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R: ...
    @overload
    def __call__(self, *args: Any, mapper: Callable[[R], Any], **kwargs: Any) -> Any: ...


def mappable[**P, R](f: Callable[P, R]) -> Mappable[P, R]:
    """Let a caller hand the result straight to a shape it wants: f(mapper=dict)."""

    @wraps(f)
    def wrapper(*args: Any, mapper: Callable[[R], Any] | None = None, **kwargs: Any) -> Any:
        result = f(*args, **kwargs)
        return result if mapper is None else mapper(result)

    return cast(Mappable[P, R], wrapper)


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
