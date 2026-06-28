import sqlite3
from contextlib import contextmanager
from datetime import date, datetime

from sqlalchemy.engine.url import make_url

from orc import config


def delete_presence(names, before, force):
    with _theme_override_conn() as conn:
        conn.executemany(
            "DELETE FROM orc_presence WHERE name = ? AND (last_seen < ? or TRUE = ?)",
            [(name, before.isoformat(), force) for name in names],
        )


def delete_theme_override():
    with _theme_override_conn() as conn:
        conn.execute("DELETE FROM orc_theme_override WHERE id = 0")


def fetch_presence():
    with _theme_override_conn() as conn:
        rows = conn.execute("SELECT name, last_seen FROM orc_presence").fetchall()
    return {name: datetime.fromisoformat(last_seen) for name, last_seen in rows}


def fetch_lg_tv_client_key(hostname):
    with _theme_override_conn() as conn:
        row = conn.execute("SELECT client_key FROM orc_lg_tv WHERE hostname = ?", (hostname,)).fetchone()
    return row[0] if row else None


def fetch_theme_override():
    with _theme_override_conn() as conn:
        row = conn.execute("SELECT name, start, end FROM orc_theme_override WHERE id = 0").fetchone()
    if not row:
        return None
    return (row[0], date.fromisoformat(row[1]), date.fromisoformat(row[2]))


def init_db():
    with _theme_override_conn() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS orc_theme_override "
            "(id INTEGER PRIMARY KEY CHECK (id = 0), name TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL)"
        )
        conn.execute("CREATE TABLE IF NOT EXISTS orc_presence (name TEXT PRIMARY KEY, last_seen TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS orc_light (device_id INTEGER PRIMARY KEY, type TEXT, state TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS orc_lg_tv (hostname TEXT PRIMARY KEY, client_key TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS orc_durations (name TEXT PRIMARY KEY, samples INTEGER NOT NULL, avg REAL NOT NULL)")


def insert_lg_tv_client_key(hostname, client_key):
    with _theme_override_conn() as conn:
        conn.execute(
            "INSERT INTO orc_lg_tv (hostname, client_key) VALUES (?, ?) "
            "ON CONFLICT(hostname) DO UPDATE SET client_key=excluded.client_key",
            (hostname, client_key),
        )


def insert_presence(names, when):
    with _theme_override_conn() as conn:
        conn.executemany(
            "INSERT INTO orc_presence (name, last_seen) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET last_seen=excluded.last_seen",
            [(name, when.isoformat()) for name in names],
        )


def insert_theme_override(override):
    with _theme_override_conn() as conn:
        conn.execute(
            "INSERT INTO orc_theme_override (id, name, start, end) VALUES (0, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, start=excluded.start, end=excluded.end",
            (override[0], override[1].isoformat(), override[2].isoformat()),
        )


def delete_all_presence(before):
    with _theme_override_conn() as conn:
        conn.execute("DELETE FROM orc_presence WHERE last_seen < ?", (before.isoformat(),))


def read_light(light):
    with _theme_override_conn() as conn:
        row = conn.execute("SELECT type, state FROM orc_light WHERE device_id = ?", (light.value,)).fetchone()
    return row if row else (None, None)


def read_lights():
    with _theme_override_conn() as conn:
        return conn.execute("SELECT device_id, state FROM orc_light WHERE state IS NOT NULL").fetchall()


@contextmanager
def _theme_override_conn():
    conn = sqlite3.connect(make_url(config.jobs_db).database)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


_ALPHA = 0.3


def update_avg(name, duration):
    sql = """
    INSERT INTO orc_durations (name, samples, avg) VALUES (?, 1, ?)
    ON CONFLICT(name) DO UPDATE SET samples = samples + 1, avg = ? * ? + (1 - ?) * avg WHERE name = ?;
    """
    with _theme_override_conn() as conn:
        conn.execute(sql, (name, duration, duration, _ALPHA, _ALPHA, name))


def fetch_durations():
    with _theme_override_conn() as conn:
        return conn.execute("SELECT name, avg FROM orc_durations").fetchall()


def write_light(light, *, type=None, state=None):
    with _theme_override_conn() as conn:
        conn.execute(
            "INSERT INTO orc_light (device_id, type, state) VALUES (?, ?, ?) "
            "ON CONFLICT(device_id) DO UPDATE SET "
            "type = COALESCE(excluded.type, orc_light.type), "
            "state = COALESCE(excluded.state, orc_light.state)",
            (light.value, type, str(state) if state is not None else None),
        )
