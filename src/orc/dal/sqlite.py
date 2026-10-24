import sqlite3
from datetime import date

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
    "CREATE TABLE IF NOT EXISTS orc_ble_tags "
    "(person TEXT PRIMARY KEY, eik BLOB NOT NULL, anchor INTEGER NOT NULL, offset INTEGER, heard INTEGER)",
    "CREATE TABLE IF NOT EXISTS orc_push_subscriptions (endpoint TEXT PRIMARY KEY, public_key TEXT NOT NULL, auth_secret TEXT NOT NULL)",
)
_DELETE_PUSH = "DELETE FROM orc_push_subscriptions WHERE endpoint = ?"
_DELETE_OVERRIDE = "DELETE FROM orc_theme_override WHERE id = 0"
_SELECT_PUSH = "SELECT endpoint, public_key, auth_secret FROM orc_push_subscriptions"
_SELECT_TAGS = "SELECT person, eik, anchor, offset, heard FROM orc_ble_tags"
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
_UPDATE_CLOCK = "UPDATE orc_ble_tags SET offset = ?, heard = ? WHERE person = ? AND (offset IS NULL OR abs(? - offset) > ?)"
_UPSERT_TAG_KEY = (
    "INSERT INTO orc_ble_tags (person, eik, anchor) VALUES (?, ?, ?) "
    "ON CONFLICT(person) DO UPDATE SET eik = excluded.eik, anchor = excluded.anchor, offset = NULL, heard = NULL"
)
_UPSERT_DURATION = (
    "INSERT INTO orc_durations (name, samples, avg) VALUES (?, ?, ?) "
    "ON CONFLICT(name) DO UPDATE SET samples = excluded.samples, avg = excluded.avg"
)

_durations: dict[str, tuple[int, float]] = {}
_pending: list[str] = []


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
def fetch_tags(conn: sqlite3.Connection) -> dict[str, tuple[m.BleKey, m.TagClock | None]]:
    rows = conn.execute(_SELECT_TAGS).fetchall()
    return {
        person: (m.BleKey(bytes(eik), anchor), m.TagClock(offset, heard) if heard is not None else None)
        for person, eik, anchor, offset, heard in rows
    }


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
def load_durations(conn: sqlite3.Connection) -> None:
    _pending.clear()
    _durations.clear()
    _durations.update({name: (samples, avg) for name, samples, avg in conn.execute(_SELECT_DURATIONS)})


@connected
def insert_push_subscription(conn: sqlite3.Connection, subscription: m.PushSubscription) -> None:
    conn.execute(_UPSERT_PUSH, subscription)


@connected
def insert_theme_override(conn: sqlite3.Connection, override: tuple[str, date, date]) -> None:
    conn.execute(_UPSERT_OVERRIDE, (override[0], override[1].isoformat(), override[2].isoformat()))


@connected
def upsert_tag_key(conn: sqlite3.Connection, person: str, key: m.BleKey) -> None:
    conn.execute(_UPSERT_TAG_KEY, (person, key.eik, key.anchor))


@connected
def update_tag_clock(conn: sqlite3.Connection, person: str, clock: m.TagClock) -> None:
    conn.execute(_UPDATE_CLOCK, (clock.offset, clock.heard, person, clock.offset, _CLOCK_MOVED_SECONDS))


def durations() -> dict[str, tuple[int, float]]:
    return dict(_durations)


def update_avg(name: str, duration: float) -> None:
    global _pending
    samples, avg = _durations.get(name, (0, duration))
    _durations[name] = (samples + 1, _ALPHA * duration + (1 - _ALPHA) * avg)
    _pending.append(name)
    if len(_pending) >= _DURATION_BATCH:
        batch, _pending = _pending, []
        with connection() as conn:
            conn.executemany(_UPSERT_DURATION, [(n, *_durations[n]) for n in set(batch)])
