import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from aiowebostv import WebOsClient
from orc_plugins.lgtv.dal import sqlite
from orc_plugins.lgtv.dal.sqlite import Connection

from orc.model import DeviceEnum


def pair(connection: Connection, hostname: str) -> str | None:
    key = asyncio.run(_pair(hostname))
    if key is None:
        print(f"LG TV pairing not completed for {hostname}", file=sys.stderr)
        return None
    sqlite.insert_client_key(connection, hostname, key)
    return key


def is_off(tv: DeviceEnum) -> bool:
    return not asyncio.run(_is_port_open(tv.value, 3000, timeout=0.5))


def off(connection: Connection, tv: DeviceEnum) -> None:
    client_key = sqlite.fetch_client_key(connection, tv.value)
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
