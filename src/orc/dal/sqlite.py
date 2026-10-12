import sqlite3
from datetime import date
from typing import Any

from orc import model as m
from orc.decorators import connected, connection

_ALPHA: float = 0.3
_CLOCK_MOVED_SECONDS = 512
_DURATION_BATCH = 50

_SCHEMA = (
    "PRAGMA journal_mode=WAL",
    "CREATE TABLE IF NOT EXISTS orc_theme_override "
    "(id INTEGER PRIMARY KEY CHECK (id = 0), name TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS orc_durations (name TEXT PRIMARY KEY, samples INTEGER NOT NULL, avg REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS orc_ble_clocks (person TEXT PRIMARY KEY, offset INTEGER NOT NULL, heard INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS orc_push_subscriptions (endpoint TEXT PRIMARY KEY, public_key TEXT NOT NULL, auth_secret TEXT NOT NULL)",
)
_DELETE_PUSH = "DELETE FROM orc_push_subscriptions WHERE endpoint = ?"
_DELETE_OVERRIDE = "DELETE FROM orc_theme_override WHERE id = 0"
_SELECT_PUSH = "SELECT endpoint, public_key, auth_secret FROM orc_push_subscriptions"
_SELECT_CLOCKS = "SELECT person, offset, heard FROM orc_ble_clocks"
_SELECT_OVERRIDE = "SELECT name, start, end FROM orc_theme_override WHERE id = 0"
_SELECT_DURATIONS = "SELECT name, samples, avg FROM orc_durations ORDER BY name"
_UPSERT_PUSH = (
    "INSERT INTO orc_push_subscriptions (endpoint, public_key, auth_secret) VALUES (?, ?, ?) "
    "ON CONFLICT(endpoint) DO UPDATE SET public_key=excluded.public_key, auth_secret=excluded.auth_secret"
)
_UPSERT_OVERRIDE = (
    "INSERT INTO orc_theme_override (id, name, start, end) VALUES (0, ?, ?, ?) "
    "ON CONFLICT(id) DO UPDATE SET name=excluded.name, start=excluded.start, end=excluded.end"
)
_UPSERT_CLOCK = (
    "INSERT INTO orc_ble_clocks (person, offset, heard) VALUES (?, ?, ?) "
    "ON CONFLICT(person) DO UPDATE SET offset = excluded.offset, heard = excluded.heard "
    f"WHERE abs(excluded.offset - orc_ble_clocks.offset) > {_CLOCK_MOVED_SECONDS}"
)
_UPDATE_AVG = (
    "INSERT INTO orc_durations (name, samples, avg) VALUES (?, 1, ?) "
    f"ON CONFLICT(name) DO UPDATE SET samples = samples + 1, avg = {_ALPHA} * excluded.avg + {1 - _ALPHA} * avg"
)

_durations: list[tuple[str, float]] = []


@connected
def delete_push_subscription(conn: sqlite3.Connection, endpoint: str) -> None:
    conn.execute(_DELETE_PUSH, (endpoint,))


@connected
def delete_theme_override(conn: sqlite3.Connection) -> None:
    conn.execute(_DELETE_OVERRIDE)


@connected
def fetch_push_subscriptions(conn: sqlite3.Connection) -> list[m.PushSubscription]:
    return [m.PushSubscription(*row) for row in conn.execute(_SELECT_PUSH)]


@connected
def fetch_tag_clocks(conn: sqlite3.Connection) -> dict[str, m.TagClock]:
    rows = conn.execute(_SELECT_CLOCKS).fetchall()
    return {person: m.TagClock(offset, heard) for person, offset, heard in rows}


@connected
def fetch_theme_override(conn: sqlite3.Connection) -> tuple[str, date, date] | None:
    row = conn.execute(_SELECT_OVERRIDE).fetchone()
    if not row:
        return None
    return (row[0], date.fromisoformat(row[1]), date.fromisoformat(row[2]))


@connected
def init_db(conn: sqlite3.Connection) -> None:
    for statement in _SCHEMA:
        conn.execute(statement)


@connected
def insert_push_subscription(conn: sqlite3.Connection, subscription: m.PushSubscription) -> None:
    conn.execute(_UPSERT_PUSH, subscription)


@connected
def insert_theme_override(conn: sqlite3.Connection, override: tuple[str, date, date]) -> None:
    conn.execute(_UPSERT_OVERRIDE, (override[0], override[1].isoformat(), override[2].isoformat()))


@connected
def upsert_tag_clock(conn: sqlite3.Connection, person: str, clock: m.TagClock) -> None:
    conn.execute(_UPSERT_CLOCK, (person, clock.offset, clock.heard))


def update_avg(name: str, duration: float) -> None:
    global _durations
    _durations.append((name, duration))
    if len(_durations) >= _DURATION_BATCH:
        batch, _durations = _durations, []
        with connection() as conn:
            conn.executemany(_UPDATE_AVG, batch)


@connected
def fetch_durations(conn: sqlite3.Connection) -> list[Any]:
    return conn.execute(_SELECT_DURATIONS).fetchall()
