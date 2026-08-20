from collections.abc import Callable
from typing import Any

type Connection = Callable[[], Any]


def init_db(connection: Connection) -> None:
    pass


def fetch_thing(connection: Connection, key: str) -> str | None:
    pass


def insert_thing(connection: Connection, key: str, value: str) -> None:
    pass
