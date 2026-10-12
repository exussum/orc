import sqlite3

from orc.plugins import connected


@connected
def init_db(conn: sqlite3.Connection) -> None:
    # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query -- static DDL literal, no interpolation
    conn.execute("CREATE TABLE IF NOT EXISTS orc_lg_tv (hostname TEXT PRIMARY KEY, client_key TEXT NOT NULL)")


@connected
def fetch_client_key(conn: sqlite3.Connection, hostname: str) -> str | None:
    row = conn.execute("SELECT client_key FROM orc_lg_tv WHERE hostname = ?", (hostname,)).fetchone()
    return row[0] if row else None


@connected
def insert_client_key(conn: sqlite3.Connection, hostname: str, client_key: str) -> None:
    conn.execute(
        "INSERT INTO orc_lg_tv (hostname, client_key) VALUES (?, ?) ON CONFLICT(hostname) DO UPDATE SET client_key=excluded.client_key",
        (hostname, client_key),
    )
