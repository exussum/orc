# Separate from api.py to avoid a circular import: api.py imports these decorators,
# so anything that imports api must not live here.

import contextlib
import os
import threading
from functools import wraps
from pathlib import Path
from types import SimpleNamespace

from mistletoe import Document

from orc import model as m
from orc.collections import doc_to_sub_tables
from orc.model import column_to_value

audio_lock = threading.Lock()


@contextlib.contextmanager
def silence_fd(fd):
    saved = os.dup(fd)
    with open(os.devnull, "w") as devnull:
        os.dup2(devnull.fileno(), fd)
        try:
            yield
        finally:
            os.dup2(saved, fd)
            os.close(saved)


def requires_ctx(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if kwargs.get("ctx") is None:
            raise ValueError("ctx must be injected by the executor")
        return f(*args, **kwargs)

    return wrapper


def synchronized(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


_UNSET = object()
_FAILED = object()


def plugin_config(name, *, schema):
    def decorator(fn):
        cache = _UNSET

        @wraps(fn)
        def wrapper(ctx, *args, **kwargs):
            nonlocal cache
            if cache is _UNSET:
                try:
                    cache = _load_plugin_config(name, ctx.config.config_dir, schema)
                except Exception:
                    cache = _FAILED
            if cache is not _FAILED:
                return fn(ctx, cache, *args, **kwargs)

        wrapper._config = name
        return wrapper

    return decorator


def _load_plugin_config(name, config_dir, schema):
    path = Path(config_dir) / "plugins" / f"{name}.md"
    with open(path) as fh:
        doc = Document(fh)

    attrs = {}
    for section, columns in schema.items():
        for trigger, rows in doc_to_sub_tables(doc, section, columns, cast=column_to_value):
            attrs[trigger] = rows if len(rows) > 1 else rows[0]

    return SimpleNamespace(**attrs)


def unwrap_rule_container(f):
    def wrapper(*args):
        if isinstance(args[0], m.Routine | m.Configs):
            for e in args[0].items:
                f(e, *args[1:])
        elif len(args) > 1 and isinstance(args[1], m.Routine | m.Configs):
            for e in args[1].items:
                f(args[0], e, *args[2:])
        else:
            f(*args)

    return wrapper
