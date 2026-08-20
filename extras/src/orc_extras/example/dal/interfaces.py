from typing import Protocol

from orc_extras.example.dal.sqlite import Connection


class FooService(Protocol):
    def do_foo(self, connection: Connection, key: str, arg: str, timeout: int) -> int: ...


class BarService(Protocol):
    def do_bar(self, key: str, arg: str, timeout: int) -> str: ...
