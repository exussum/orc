import asyncio
from contextlib import asynccontextmanager, suppress

from aiowebostv import WebOsClient

from orc.dal._decorators import requires_enabled
from orc.dal.sqlite import fetch_lg_tv_client_key


def is_off(tv):
    return not asyncio.run(_is_port_open(tv.value, 3000, timeout=0.5))


@requires_enabled(None)
def off(tv):
    client_key = fetch_lg_tv_client_key(tv.value)
    if not client_key:
        raise RuntimeError(f"No client_key for {tv.value} in orc_lg_tv; run the Pair LG TV plugin first")
    asyncio.run(_power_off(tv.value, client_key))


async def _power_off(host, client_key):
    if not await _is_port_open(host, 3000, timeout=0.5):
        return
    try:
        async with _webos_client(host, client_key) as c:
            await c.power_off()
    except Exception:
        if await _is_port_open(host, 3000, timeout=1.0):
            raise


@asynccontextmanager
async def _webos_client(host, client_key):
    client = WebOsClient(host, client_key)
    await client.connect()
    try:
        yield client
    finally:
        with suppress(Exception):
            await client.disconnect()


async def _is_port_open(host, port, timeout):
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, asyncio.TimeoutError):
        return False
