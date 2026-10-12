import sqlite3

from orc.plugins import connected


@connected
def init_db(conn: sqlite3.Connection) -> None:
    # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query -- static DDL literal, no interpolation
    conn.execute("CREATE TABLE IF NOT EXISTS orc_travel_places (address TEXT PRIMARY KEY, lat REAL NOT NULL, lon REAL NOT NULL)")


@connected
def fetch_geocode(conn: sqlite3.Connection, address: str) -> tuple[float, float] | None:
    row = conn.execute("SELECT lat, lon FROM orc_travel_places WHERE address = ?", (address,)).fetchone()
    return (row[0], row[1]) if row else None


@connected
def insert_geocode(conn: sqlite3.Connection, address: str, lat: float, lon: float) -> None:
    conn.execute(
        "INSERT INTO orc_travel_places (address, lat, lon) VALUES (?, ?, ?) ON CONFLICT(address) DO NOTHING",
        (address, lat, lon),
    )


@connected
def delete_geocode(conn: sqlite3.Connection, address: str) -> None:
    conn.execute("DELETE FROM orc_travel_places WHERE address = ?", (address,))
