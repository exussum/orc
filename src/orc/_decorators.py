# Separate from api.py to avoid a circular import: api.py imports these decorators,
# so anything that imports api must not live here.

import contextlib
import os
import sys
import threading
from collections.abc import Callable, Iterator
from functools import wraps
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from mistletoe import Document

from orc import model as m
from orc.collections import doc_to_sub_tables
from orc.model import column_to_value

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
        def wrapper(ctx: Any, *args: Any, **kwargs: Any) -> R | None:
            nonlocal cache
            if cache is _UNSET:
                try:
                    cache = _load_plugin_config(resolved, ctx.config.config_dir, schema)
                except Exception as exc:
                    print(f"Failed to load plugin config {resolved!r}: {exc}", file=sys.stderr)
                    cache = _FAILED
            if cache is not _FAILED:
                return fn(ctx, cache, *args, **kwargs)
            return None

        wrapper._config = resolved  # type: ignore[attr-defined]  # attach resolved config name onto the wrapper
        return wrapper

    return decorator


def _load_plugin_config(name: str, config_dir: str, schema: dict[str, tuple[str, ...]]) -> SimpleNamespace:
    path = Path(config_dir) / "plugins" / f"{name}.md"
    with open(path) as fh:
        doc = Document(fh)

    attrs: dict[str, Any] = {}
    for section, columns in schema.items():
        if len(columns) == 2:
            col_attr = columns[1].lower()
            for trigger, rows in doc_to_sub_tables(doc, section, columns, cast=column_to_value):
                attrs[trigger] = getattr(rows[0], col_attr)
        else:
            attrs[section.lower()] = SimpleNamespace(
                **{trigger: rows for trigger, rows in doc_to_sub_tables(doc, section, columns, cast=column_to_value)}
            )

    return SimpleNamespace(**attrs)


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
