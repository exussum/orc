# Separate from api.py to avoid a circular import: api.py imports these decorators,
# so anything that imports api must not live here.

import contextlib
import os
import threading
from functools import wraps

from orc import model as m

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
