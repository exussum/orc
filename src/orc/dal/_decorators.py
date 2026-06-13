import os
import sys
from functools import wraps


def requires_enabled(stub):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not os.getenv("ORC_ENABLED"):
                print(f"[disabled] {fn.__name__} args={args} kwargs={kwargs}", file=sys.stderr)
                return stub(*args, **kwargs) if callable(stub) else stub
            return fn(*args, **kwargs)

        return wrapper

    return deco
