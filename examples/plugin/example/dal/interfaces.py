from typing import Protocol


class FooService(Protocol):
    def do_foo(self, key: str, arg: str, timeout: int) -> int: ...


class BarService(Protocol):
    def do_bar(self, key: str, arg: str, timeout: int) -> str: ...
