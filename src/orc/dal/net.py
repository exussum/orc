import asyncio
import socket
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from scapy.layers.dns import DNS, DNSQR
from scapy.layers.inet import IP, UDP
from scapy.layers.l2 import ARP, Ether
from scapy.sendrecv import AsyncSniffer, sendp

from orc import model as m
from orc.security import FMDN_ROTATION_SECONDS, FMDN_SERVICE_UUID, fmdn_parse, fmdn_resolve

_WINDOW_SECONDS = 3
# A tag can't be solicited like the LAN probes' targets - it advertises every ~2s
# and orc can only listen, so the BLE window must span several cycles to hear one.
_BLE_WINDOW_SECONDS = 10


def _resolve_targets(
    pairs: list[tuple[str, str, str]],
) -> tuple[dict[str, tuple[str, str]], list[tuple[str, Exception]]]:
    """Resolve each (name, host, mac) to an IP, concurrently.

    gethostbyname blocks on the OS resolver (~5s per unanswered name), so run the
    lookups in parallel: N slow resolutions collapse to one resolver timeout of
    wall-clock instead of summing. Threads run only stdlib DNS, never scapy.
    Returns (ip -> (person name, MAC), [(name, resolution error)]).
    """

    def resolve(entry: tuple[str, str, str]) -> tuple[str, str, str | Exception]:
        name, host, mac = entry
        try:
            return name, mac, socket.gethostbyname(host)
        except Exception as exc:
            return name, mac, exc

    targets: dict[str, tuple[str, str]] = {}  # resolved IP -> (person name, MAC)
    errors: list[tuple[str, Exception]] = []
    with ThreadPoolExecutor(max_workers=max(1, len(pairs))) as pool:
        for name, mac, res in pool.map(resolve, pairs):
            if isinstance(res, Exception):
                errors.append((name, res))
            else:
                targets[res] = (name, mac)
    return targets, errors


def _probe_lan(targets: dict[str, tuple[str, str]]) -> set[str]:
    """Probe the given IPs and return the names of those that answer on the LAN.

    A device counts as present if its IP appears as an ARP reply or an mDNS
    packet during the sniff window.
    """
    if not targets:
        return set()
    # Unicast the who-has at each device's MAC (a unicast frame makes the AP wake a
    # Wi-Fi power-save phone that ignores broadcast ARP), plus a multicast mDNS query:
    # a power-save iPhone that ignores ARP will often still answer Bonjour. Sniff both
    # passively, so any ARP or mDNS the device emits also counts. Re-send once a second
    # across the window: a single probe or reply is easily dropped, so repeat.
    probes: list = [Ether(dst=mac) / ARP(pdst=ip, hwdst=mac) for ip, (_, mac) in targets.items()]
    probes.append(
        Ether(dst="01:00:5e:00:00:fb")
        / IP(dst="224.0.0.251", ttl=255)
        / UDP(sport=5353, dport=5353)
        / DNS(rd=0, qd=DNSQR(qname="_services._dns-sd._udp.local", qtype="PTR"))
    )
    sniffer = AsyncSniffer(filter="arp or udp port 5353", store=True, timeout=_WINDOW_SECONDS)
    sniffer.start()
    # Burst the whole probe list at once, repeat once a second across the window.
    # sendp's `inter` spaces *every* packet, so it would scale with the target
    # count; a manual loop keeps the window fixed regardless of how many devices
    # we track.
    for _ in range(_WINDOW_SECONDS):
        sendp(probes, verbose=False)
        time.sleep(1)
    sniffer.join()
    responded: set[str] = set()
    for p in sniffer.results or []:
        if ARP in p and p[ARP].op == 2:
            responded.add(p[ARP].psrc)
        elif IP in p:
            responded.add(p[IP].src)
    return {name for ip, (name, _) in targets.items() if ip in responded}


def scan_presence(
    pairs: list[tuple[str, str, str]], tags: Mapping[str, m.BleKey], now: datetime
) -> tuple[set[str], list[tuple[str, Exception]]]:
    """Return the person names heard on the LAN or over BLE, plus any scan failures.

    For each (name, host, mac), resolves host to an IP and probes it. A device counts as
    present if its IP appears as an ARP reply or an mDNS packet during the sniff window.
    Each tag's rotating EID is matched against the BLE advertisements heard meanwhile.
    """
    with ThreadPoolExecutor(max_workers=2) as pool:
        heard = pool.submit(scan_ble, tags, now)
        lan = pool.submit(_scan_lan, pairs)
    names, errors = lan.result()
    try:
        names |= heard.result()
    except Exception as exc:
        errors.append(("BLE", exc))
    return names, errors


def scan_ble(tags: Mapping[str, m.BleKey], now: datetime) -> set[str]:
    if not tags:
        return set()
    heard = asyncio.run(_scan_ble())
    ts = int(now.timestamp())
    # The tag's clock zero is its pair date, so the current counter is now - anchor;
    # the ±1 neighbour windows cover boundary timing and the tag's crystal drift.
    return {
        person
        for person, key in tags.items()
        for expected in (ts - key.anchor,)
        if fmdn_resolve(heard, key.eik, (expected - FMDN_ROTATION_SECONDS, expected, expected + FMDN_ROTATION_SECONDS))
    }


def _scan_lan(pairs: list[tuple[str, str, str]]) -> tuple[set[str], list[tuple[str, Exception]]]:
    targets, errors = _resolve_targets(pairs)
    return _probe_lan(targets), errors


async def _scan_ble() -> set[bytes]:
    heard: set[bytes] = set()

    def seen(device: BLEDevice, data: AdvertisementData) -> None:
        frame = data.service_data.get(FMDN_SERVICE_UUID)
        parsed = fmdn_parse(frame) if frame else None
        if parsed:
            heard.add(parsed)

    async with BleakScanner(seen):
        await asyncio.sleep(_BLE_WINDOW_SECONDS)
    return heard
