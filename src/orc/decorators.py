# Separate from api.py to avoid a circular import: api.py imports these decorators,
# so anything that imports api must not live here.

import contextlib
import os
import sys
import threading
from collections.abc import Callable, Iterator
from functools import wraps
from typing import Any

from orc import model as m
from orc.declarations import load_plugin_config

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


def requires_enabled[**P, R](stub: Any) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def deco(fn: Callable[P, R]) -> Callable[P, R]:
        @wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            if not os.getenv("ORC_ENABLED"):
                print(f"[disabled] {fn.__name__} args={args} kwargs={kwargs}", file=sys.stderr)
                return stub(*args, **kwargs) if callable(stub) else stub
            return fn(*args, **kwargs)

        return wrapper

    return deco


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


_UNSET = object()
_FAILED = object()


def plugin_config[R](name: str, *, schema: dict[str, tuple[str, ...]]) -> Callable[[Callable[..., R]], Callable[..., R | None]]:
    def decorator(fn: Callable[..., R]) -> Callable[..., R | None]:
        if fn.__module__.split(".")[0] != "orc" and "/" not in name:
            package = fn.__module__.split(".")[0]
            resolved = f"{package}/{name}"
        else:
            resolved = name
        cache: Any = _UNSET

        @wraps(fn)
        def wrapper(ctx: m.AppContext, *args: Any, **kwargs: Any) -> R | None:
            nonlocal cache
            if cache is _UNSET:
                try:
                    cache = load_plugin_config(resolved, ctx.config.config_dir, schema)
                except Exception as exc:
                    print(f"Failed to load plugin config {resolved!r}: {exc}", file=sys.stderr)
                    cache = _FAILED
            if cache is not _FAILED:
                return fn(ctx, cache, *args, **kwargs)
            return None

        wrapper._config = resolved  # type: ignore[attr-defined]  # attach resolved config name onto the wrapper
        return wrapper

    return decorator


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
