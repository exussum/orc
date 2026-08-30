import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any

from sqlalchemy.engine.url import make_url

import orc

_ALPHA: float = 0.3


def delete_presence(names: Iterable[str], before: datetime, force: bool) -> None:
    with connection() as conn:
        conn.executemany(
            "DELETE FROM orc_presence WHERE name = ? AND (last_seen < ? or TRUE = ?)",
            [(name, before.isoformat(), force) for name in names],
        )


def delete_theme_override() -> None:
    with connection() as conn:
        conn.execute("DELETE FROM orc_theme_override WHERE id = 0")


def fetch_presence() -> dict[str, datetime]:
    with connection() as conn:
        rows = conn.execute("SELECT name, last_seen FROM orc_presence").fetchall()
    return {name: datetime.fromisoformat(last_seen) for name, last_seen in rows}


def fetch_theme_override() -> tuple[str, date, date] | None:
    with connection() as conn:
        row = conn.execute("SELECT name, start, end FROM orc_theme_override WHERE id = 0").fetchone()
    if not row:
        return None
    return (row[0], date.fromisoformat(row[1]), date.fromisoformat(row[2]))


def init_db() -> None:
    with connection() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS orc_theme_override "
            "(id INTEGER PRIMARY KEY CHECK (id = 0), name TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL)"
        )
        conn.execute("CREATE TABLE IF NOT EXISTS orc_presence (name TEXT PRIMARY KEY, last_seen TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS orc_durations (name TEXT PRIMARY KEY, samples INTEGER NOT NULL, avg REAL NOT NULL)")


def insert_presence(names: Iterable[str], when: datetime) -> None:
    with connection() as conn:
        conn.executemany(
            "INSERT INTO orc_presence (name, last_seen) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET last_seen=excluded.last_seen",
            [(name, when.isoformat()) for name in names],
        )


def insert_theme_override(override: tuple[str, date, date]) -> None:
    with connection() as conn:
        conn.execute(
            "INSERT INTO orc_theme_override (id, name, start, end) VALUES (0, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, start=excluded.start, end=excluded.end",
            (override[0], override[1].isoformat(), override[2].isoformat()),
        )


def delete_all_presence(before: datetime) -> None:
    with connection() as conn:
        conn.execute("DELETE FROM orc_presence WHERE last_seen < ?", (before.isoformat(),))


def update_avg(name: str, duration: float) -> None:
    sql = """
    INSERT INTO orc_durations (name, samples, avg) VALUES (?, 1, ?)
    ON CONFLICT(name) DO UPDATE SET samples = samples + 1, avg = ? * ? + (1 - ?) * avg WHERE name = ?;
    """
    with connection() as conn:
        conn.execute(sql, (name, duration, duration, _ALPHA, _ALPHA, name))


def fetch_durations() -> list[Any]:
    with connection() as conn:
        return conn.execute("SELECT name, samples, avg FROM orc_durations ORDER BY name").fetchall()


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    # Public DB connection context manager; plugins use it to own their own tables.
    db_path = make_url(orc.config.settings.jobs_db).database
    assert db_path is not None  # a configured sqlite URL always includes a path
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()
