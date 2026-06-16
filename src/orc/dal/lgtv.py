import asyncio
import sys

from aiowebostv import WebOsClient

from orc.dal._decorators import requires_enabled
from orc.dal.sqlite import insert_lg_tv_client_key


@requires_enabled(None)
def pair(hostname):
    key = asyncio.run(_pair(hostname))
    if key is None:
        print(f"LG TV pairing not completed for {hostname}", file=sys.stderr)
        return None
    insert_lg_tv_client_key(hostname, key)
    return key


async def _pair(hostname):
    client = WebOsClient(hostname, None)
    try:
        await client.connect()
        return client.client_key
    finally:
        await client.disconnect()
