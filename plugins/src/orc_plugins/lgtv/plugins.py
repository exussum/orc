"""LG WebOS TV control, pairing, and client-key storage (aiowebostv).

Owns its own ``orc_lg_tv`` table via core's ``api.connection()`` helper;
``init_db`` is registered as a boot hook so the table exists before use.
"""

import asyncio
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from typing import Any

from aiowebostv import WebOsClient

from orc.decorators import requires_enabled
from orc.model import AppContext, DeviceEnum

# Callers thread in core's ``api.connection`` (a context-manager factory) from their
# ctx; this module never imports orc.api itself.
type Connection = Callable[[], Any]


def init_db(connection: Connection) -> None:
    with connection() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS orc_lg_tv (hostname TEXT PRIMARY KEY, client_key TEXT NOT NULL)")


def pair_tv(ctx: AppContext, *, device: str) -> None:
    # Driven by a device-row click, which passes the TV via ?device=<name>.
    pair(ctx.api.connection, ctx.orc.WebOS[device].value)


@requires_enabled(None)
def pair(connection: Connection, hostname: str) -> str | None:
    key = asyncio.run(_pair(hostname))
    if key is None:
        print(f"LG TV pairing not completed for {hostname}", file=sys.stderr)
        return None
    _insert_client_key(connection, hostname, key)
    return key


def is_off(tv: DeviceEnum) -> bool:
    return not asyncio.run(_is_port_open(tv.value, 3000, timeout=0.5))


@requires_enabled(None)
def off(connection: Connection, tv: DeviceEnum) -> None:
    client_key = _fetch_client_key(connection, tv.value)
    if not client_key:
        raise RuntimeError(f"No client_key for {tv.value} in orc_lg_tv; run the Pair LG TV plugin first")
    asyncio.run(_power_off(tv.value, client_key))


async def _pair(hostname: str) -> str | None:
    client = WebOsClient(hostname, None)
    try:
        await client.connect()
        return client.client_key
    finally:
        await client.disconnect()


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


def _fetch_client_key(connection: Connection, hostname: str) -> str | None:
    with connection() as conn:
        row = conn.execute("SELECT client_key FROM orc_lg_tv WHERE hostname = ?", (hostname,)).fetchone()
    return row[0] if row else None


def _insert_client_key(connection: Connection, hostname: str, client_key: str) -> None:
    with connection() as conn:
        conn.execute(
            "INSERT INTO orc_lg_tv (hostname, client_key) VALUES (?, ?) "
            "ON CONFLICT(hostname) DO UPDATE SET client_key=excluded.client_key",
            (hostname, client_key),
        )


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
