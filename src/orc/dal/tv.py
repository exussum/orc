import asyncio
import socket
import sys
from contextlib import asynccontextmanager, suppress

from aiowebostv import WebOsClient
from getmac import get_mac_address
from wakeonlan import send_magic_packet

from orc._locked_dict import LockedDict
from orc.dal._decorators import requires_enabled, retry_async
from orc.dal.sqlite import fetch_lg_tv_client_key

_macs = LockedDict()


@requires_enabled({})
def fetch_macs(tvs):
    for tv in tvs:
        mac = _resolve_mac(tv.value)
        if mac is None:
            print(f"[tv.fetch_macs] could not resolve MAC for {tv.name} ({tv.value}); 'on' will fail until refreshed", file=sys.stderr)
            continue
        _macs[tv] = mac
    return _macs.copy()


@requires_enabled(None)
def on(tv):
    mac = _macs.get(tv)
    if mac is None:
        raise RuntimeError(f"No cached MAC for {tv.name}; was the TV reachable when fetch_macs ran?")
    client_key = fetch_lg_tv_client_key(tv.value)
    if not client_key:
        raise RuntimeError(f"No client_key for {tv.value} in orc_lg_tv; run the Pair LG TV plugin first")
    send_magic_packet(mac)
    asyncio.run(_wake_and_home(tv.value, client_key))


@requires_enabled(None)
def off(tv):
    client_key = fetch_lg_tv_client_key(tv.value)
    if not client_key:
        raise RuntimeError(f"No client_key for {tv.value} in orc_lg_tv; run the Pair LG TV plugin first")
    asyncio.run(_power_off(tv.value, client_key))


async def _power_off(host, client_key):
    if not await _is_port_open(host, 3000, timeout=0.5):
        return
    await _connect_and_power_off(host, client_key)


async def _wake_and_home(host, client_key):
    await _wait_for_port(host, 3000)
    await _connect_and_launch_home(host, client_key)


@retry_async(deadline_secs=15)
async def _connect_and_power_off(host, client_key):
    async with _webos_client(host, client_key) as c:
        await c.power_off()


@retry_async(deadline_secs=30)
async def _connect_and_launch_home(host, client_key):
    async with _webos_client(host, client_key) as c:
        await c.launch_app("com.webos.app.home")


@retry_async(deadline_secs=20)
async def _wait_for_port(host, port):
    if not await _is_port_open(host, port, timeout=0.1):
        raise RuntimeError(f"TV {host}:{port} not reachable; magic packet may have been dropped")


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


def _resolve_mac(hostname):
    # connect attempt primes the OS ARP cache so get_mac_address can resolve it
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        try:
            s.connect((hostname, 3000))
        except OSError:
            pass
    return get_mac_address(hostname=hostname)
