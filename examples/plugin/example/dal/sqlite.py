import sqlite3

from orc.plugins import connected


@connected
def init_db(conn: sqlite3.Connection) -> None:
    pass


@connected
def fetch_thing(conn: sqlite3.Connection, key: str) -> str | None:
    pass


@connected
def insert_thing(conn: sqlite3.Connection, key: str, value: str) -> None:
    pass
