import asyncio
import socket
import sys

from aiowebostv import WebOsClient
from getmac import get_mac_address
from wakeonlan import send_magic_packet

from orc._locked_dict import LockedDict
from orc.dal._decorators import requires_enabled
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
    await _with_client(host, client_key, lambda c: c.power_off(), action="power_off", deadline_secs=15)


async def _wake_and_home(host, client_key):
    await _wait_for_port(host, 3000, timeout=20)
    await _with_client(host, client_key, lambda c: c.launch_app("com.webos.app.home"), action="launch_app(home)", deadline_secs=30)


async def _with_client(host, client_key, op, action, deadline_secs):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + deadline_secs
    last_err = None
    while loop.time() < deadline:
        client = WebOsClient(host, client_key)
        try:
            await client.connect()
            await op(client)
            return
        except Exception as e:
            last_err = e
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass
        await asyncio.sleep(0.1)
    raise RuntimeError(f"{action} on {host} failed within {deadline_secs}s: {last_err}")


async def _wait_for_port(host, port, timeout):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if await _is_port_open(host, port, timeout=0.1):
            return
        await asyncio.sleep(0.1)
    raise RuntimeError(f"TV {host}:{port} not reachable after {timeout}s; magic packet may have been dropped")


async def _is_port_open(host, port, timeout):
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


def _resolve_mac(hostname):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        try:
            s.connect((hostname, 3000))
        except OSError:
            pass
    return get_mac_address(hostname=hostname)
