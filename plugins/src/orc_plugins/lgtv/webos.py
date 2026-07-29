"""LG WebOS TV control, pairing, and client-key storage (aiowebostv).

Owns its own ``orc_lg_tv`` table via core's ``api.connection()`` helper;
``init_db`` is registered as a boot hook so the table exists before use.
"""

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING, Any

from aiowebostv import WebOsClient

from orc.decorators import requires_enabled

if TYPE_CHECKING:
    from orc.model import DeviceEnum

# orc.api imports orc.config at module top, which isn't ready while this plugin
# is imported during orc's config load — so import connection lazily, inside the DB calls.


def init_db() -> None:
    from orc.api import connection

    with connection() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS orc_lg_tv (hostname TEXT PRIMARY KEY, client_key TEXT NOT NULL)")


def _fetch_client_key(hostname: str) -> str | None:
    from orc.api import connection

    with connection() as conn:
        row = conn.execute("SELECT client_key FROM orc_lg_tv WHERE hostname = ?", (hostname,)).fetchone()
    return row[0] if row else None


def _insert_client_key(hostname: str, client_key: str) -> None:
    from orc.api import connection

    with connection() as conn:
        conn.execute(
            "INSERT INTO orc_lg_tv (hostname, client_key) VALUES (?, ?) "
            "ON CONFLICT(hostname) DO UPDATE SET client_key=excluded.client_key",
            (hostname, client_key),
        )


# --- power control ---


def is_off(tv: "DeviceEnum") -> bool:
    return not asyncio.run(_is_port_open(tv.value, 3000, timeout=0.5))


@requires_enabled(None)
def off(tv: "DeviceEnum") -> None:
    client_key = _fetch_client_key(tv.value)
    if not client_key:
        raise RuntimeError(f"No client_key for {tv.value} in orc_lg_tv; run the Pair LG TV plugin first")
    asyncio.run(_power_off(tv.value, client_key))


async def _power_off(host: str, client_key: str) -> None:
    if not await _is_port_open(host, 3000, timeout=0.5):
        return
    try:
        async with _webos_client(host, client_key) as c:
            await c.power_off()
    except TimeoutError:
        pass
    except Exception:
        if await _is_port_open(host, 3000, timeout=1.0):
            raise


@asynccontextmanager
async def _webos_client(host: str, client_key: str) -> AsyncIterator[Any]:
    client = WebOsClient(host, client_key)
    await client.connect()
    try:
        yield client
    finally:
        with suppress(Exception):
            await client.disconnect()


async def _is_port_open(host: str, port: int, timeout: float) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        await writer.wait_closed()
        return True
    except OSError, asyncio.TimeoutError:
        return False


# --- pairing ---


@requires_enabled(None)
def pair(hostname: str) -> str | None:
    key = asyncio.run(_pair(hostname))
    if key is None:
        print(f"LG TV pairing not completed for {hostname}", file=sys.stderr)
        return None
    _insert_client_key(hostname, key)
    return key


async def _pair(hostname: str) -> str | None:
    client = WebOsClient(hostname, None)
    try:
        await client.connect()
        return client.client_key
    finally:
        await client.disconnect()
