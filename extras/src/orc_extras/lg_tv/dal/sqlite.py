from collections.abc import Callable
from typing import Any

type Connection = Callable[[], Any]


def init_db(connection: Connection) -> None:
    with connection() as conn:
        # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query -- static DDL literal, no interpolation
        conn.execute("CREATE TABLE IF NOT EXISTS orc_lg_tv (hostname TEXT PRIMARY KEY, client_key TEXT NOT NULL)")


def fetch_client_key(connection: Connection, hostname: str) -> str | None:
    with connection() as conn:
        row = conn.execute("SELECT client_key FROM orc_lg_tv WHERE hostname = ?", (hostname,)).fetchone()
    return row[0] if row else None


def insert_client_key(connection: Connection, hostname: str, client_key: str) -> None:
    with connection() as conn:
        conn.execute(
            "INSERT INTO orc_lg_tv (hostname, client_key) VALUES (?, ?) ON CONFLICT(hostname) DO UPDATE SET client_key=excluded.client_key",
            (hostname, client_key),
        )
