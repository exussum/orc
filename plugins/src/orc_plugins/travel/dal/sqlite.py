from collections.abc import Callable
from typing import Any

type Connection = Callable[[], Any]


def init_db(connection: Connection) -> None:
    with connection() as conn:
        # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query -- static DDL literal, no interpolation
        conn.execute("CREATE TABLE IF NOT EXISTS orc_travel_places (address TEXT PRIMARY KEY, lat REAL NOT NULL, lon REAL NOT NULL)")


def fetch_geocode(connection: Connection, address: str) -> tuple[float, float] | None:
    with connection() as conn:
        row = conn.execute("SELECT lat, lon FROM orc_travel_places WHERE address = ?", (address,)).fetchone()
    return (row[0], row[1]) if row else None


def insert_geocode(connection: Connection, address: str, lat: float, lon: float) -> None:
    with connection() as conn:
        conn.execute(
            "INSERT INTO orc_travel_places (address, lat, lon) VALUES (?, ?, ?) ON CONFLICT(address) DO NOTHING",
            (address, lat, lon),
        )
