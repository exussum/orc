import asyncio
import logging
import os
import signal
import socket
import threading
import time
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, tzinfo

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from scapy.layers.dns import DNS, DNSQR
from scapy.layers.inet import IP, UDP
from scapy.layers.l2 import ARP, Ether
from scapy.sendrecv import AsyncSniffer, sendp

from orc import model as m
from orc.collections import LockedDict
from orc.security import FMDN_ROTATION_SECONDS, FMDN_SERVICE_UUID, fmdn_eids, fmdn_parse

_WINDOW_SECONDS = 3
# A tag can't be solicited like the LAN probes' targets - it only broadcasts (every
# ~6s, with multi-cycle dropouts measured up to 42s), so windowed scans miss it;
# the listener hears every frame instead and a tag in range never goes this quiet.
_BLE_FRESH_SECONDS = 180
_BLE_INDEX_REFRESH_SECONDS = 300

_log = logging.getLogger(__name__)


class BleListener:
    """Continuously hears FMDN advertisements and keeps a last-heard time per person.

    Runs a BleakScanner in its own daemon thread; every frame is one dict lookup
    against the EID index, rebuilt as the 1024s rotation windows advance. A scanner
    failure brings the process down — supervisor restarts it.
    """

    def __init__(self, tags: Mapping[str, m.BleKey], tz: tzinfo) -> None:
        self._tags = tags
        self._tz = tz
        self._index: dict[bytes, str] = {}
        self._heard: LockedDict[str, datetime] = LockedDict()

    def start(self) -> None:
        threading.Thread(target=self._listen, name="ble-listener", daemon=True).start()

    def present(self, now: datetime) -> set[str]:
        return {person for person, heard in self._heard.copy().items() if (now - heard).total_seconds() <= _BLE_FRESH_SECONDS}

    def delete_presence(self, names: Iterable[str]) -> None:
        for name in names:
            self._heard.pop(name)

    def _listen(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception:
            _log.exception("ble: listener died, restarting orc")
            os.kill(os.getppid(), signal.SIGTERM)

    async def _run(self) -> None:
        async with BleakScanner(self._seen):
            while True:
                self._index = _eid_index(self._tags, self._now())
                await asyncio.sleep(_BLE_INDEX_REFRESH_SECONDS)

    def _seen(self, device: BLEDevice, data: AdvertisementData) -> None:
        frame = data.service_data.get(FMDN_SERVICE_UUID)
        eid = fmdn_parse(frame) if frame else None
        if eid and (person := self._index.get(eid)):
            self._heard[person] = self._now()

    def _now(self) -> datetime:
        return datetime.now(tz=self._tz)


_ble_listener: BleListener | None = None


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


def delete_ble_presence(names: Iterable[str]) -> None:
    if _ble_listener:
        _ble_listener.delete_presence(names)


def start_ble_listener(tags: Mapping[str, m.BleKey], tz: tzinfo) -> None:
    global _ble_listener
    if not tags:
        return
    _ble_listener = BleListener(tags, tz)
    _ble_listener.start()


def scan_presence(pairs: list[tuple[str, str, str]], now: datetime) -> tuple[set[str], list[tuple[str, Exception]]]:
    """Return the person names heard on the LAN or over BLE, plus any scan failures.

    For each (name, host, mac), resolves host to an IP and probes it. A device counts as
    present if its IP appears as an ARP reply or an mDNS packet during the sniff window.
    BLE presence is anyone the listener heard recently enough.
    """
    targets, errors = _resolve_targets(pairs)
    names = _probe_lan(targets)
    if _ble_listener:
        names |= _ble_listener.present(now)
    return names, errors


def _eid_index(tags: Mapping[str, m.BleKey], now: datetime) -> dict[bytes, str]:
    ts = int(now.timestamp())
    index: dict[bytes, str] = {}
    for person, key in tags.items():
        # The tag's clock zero is its pair date, so the current counter is now - anchor;
        # the ±1 neighbour windows cover boundary timing and the tag's crystal drift.
        expected = ts - key.anchor
        for counter in (expected - FMDN_ROTATION_SECONDS, expected, expected + FMDN_ROTATION_SECONDS):
            for eid in fmdn_eids(key.eik, counter):
                index[eid] = person
    return index
