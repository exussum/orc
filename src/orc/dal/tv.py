import asyncio
import socket
import sys

from aiowebostv import WebOsClient
from getmac import get_mac_address
from wakeonlan import send_magic_packet

from orc import config
from orc.dal._decorators import requires_enabled

_macs: dict = {}


@requires_enabled({})
def fetch_macs(tvs):
    for tv in tvs:
        mac = _resolve_mac(tv.value)
        if mac is None:
            print(f"[tv.fetch_macs] could not resolve MAC for {tv.name} ({tv.value}); 'on' will fail until refreshed", file=sys.stderr)
            continue
        _macs[tv] = mac
    return dict(_macs)


@requires_enabled(None)
def on(tv):
    mac = _macs.get(tv)
    if mac is None:
        raise RuntimeError(f"No cached MAC for {tv.name}; was the TV reachable when fetch_macs ran?")
    send_magic_packet(mac)


@requires_enabled(None)
def off(tv):
    client_key = config.secrets.lg_tv_client_keys.get(tv.name)
    if not client_key:
        raise RuntimeError(f"No LG client_key for {tv.name} in secrets (lg_tv_client_keys)")
    asyncio.run(_power_off(tv.value, client_key))


async def _power_off(host, client_key):
    client = WebOsClient(host, client_key)
    try:
        await client.connect()
        await client.power_off()
    finally:
        await client.disconnect()


def _resolve_mac(hostname):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        try:
            s.connect((hostname, 3000))
        except OSError:
            pass
    return get_mac_address(hostname=hostname)
