import asyncio
import os
import sys
from functools import wraps


def retry_async(deadline_secs):
    def deco(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            loop = asyncio.get_running_loop()
            deadline = loop.time() + deadline_secs
            last_err = None
            while loop.time() < deadline:
                try:
                    return await fn(*args, **kwargs)
                except Exception as e:
                    last_err = e
                await asyncio.sleep(0.1)
            if last_err:
                raise last_err
            raise RuntimeError(f"{fn.__name__} timed out after {deadline_secs}s")

        return wrapper

    return deco


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
