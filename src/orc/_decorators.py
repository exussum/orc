from functools import wraps

from orc import model as m


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
