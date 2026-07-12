import asyncio
import os
import sys
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any


def retry_async[**P, R](deadline_secs: float) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    def deco(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + deadline_secs
            last_err: Exception | None = None
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
