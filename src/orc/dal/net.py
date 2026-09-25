import asyncio
import logging
import os
import signal
import socket
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, tzinfo

from bleak import BleakClient, BleakScanner
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
_BLE_INDEX_REFRESH_SECONDS = 300

_log = logging.getLogger(__name__)


class Presence:
    """Last-heard time per person, fed by BLE hearings, LAN scan results, and
    manual check-ins; a pause narrows presence to evidence heard after it.

    start() runs an FMDN listener in a daemon thread: a tag can't be solicited
    like the LAN probes' targets - it only broadcasts (every ~6s, with
    multi-cycle dropouts measured up to 42s), so windowed scans miss it; the
    listener hears every frame instead. Every frame is one dict lookup against
    the EID index, rebuilt as the 1024s rotation windows advance. A scanner
    failure brings the process down — supervisor restarts it.
    """

    def __init__(self) -> None:
        self._tags: Mapping[str, m.BleKey] = {}
        self._tz: tzinfo | None = None
        self._index: dict[bytes, str] = {}
        self._heard: LockedDict[str, datetime] = LockedDict()
        self._addresses: LockedDict[str, str] = LockedDict()
        self._paused: datetime | None = None
        self._on_change: Callable[[m.Trigger], object] = lambda trigger: None

    def start(self, tags: Mapping[str, m.BleKey], tz: tzinfo, on_change: Callable[[m.Trigger], object] | None = None) -> None:
        if on_change:
            self._on_change = on_change
        if not tags:
            return
        self._tags = tags
        self._tz = tz
        threading.Thread(target=self._listen, name="ble-listener", daemon=True).start()

    def mark(self, names: Iterable[str], when: datetime, trigger: m.Trigger) -> None:
        for name in names:
            self._heard[name] = when
        self._changed(trigger)

    def seen(self) -> dict[str, datetime]:
        return self._heard.copy()

    def present(self, cutoff: datetime) -> set[str]:
        paused = self._paused
        return {name for name, heard in self._heard.copy().items() if heard >= cutoff and (not paused or heard > paused)}

    def forget(self, names: Iterable[str], trigger: m.Trigger, before: datetime | None = None) -> None:
        # `before` keeps entries at or past it — manual check-ins stamped in the future.
        for name in names:
            if (heard := self._heard.get(name)) and (before is None or heard < before):
                self._heard.pop(name)
        self._changed(trigger)

    def pause(self, until: datetime) -> None:
        self._paused = until

    def resume(self, trigger: m.Trigger) -> None:
        self._paused = None
        self._changed(trigger)

    def probe(self, names: Iterable[str], trigger: m.Trigger) -> None:
        """One connection attempt at each tag's last-advertised address; only success marks.

        The address rotates with the EID (~17 min), so a long-silent tag times out
        and stays unmarked — no evidence, not absence.
        """

        async def connect(address: str) -> None:
            async with BleakClient(address, timeout=_WINDOW_SECONDS):
                pass

        for person in names:
            if address := self._addresses.get(person):
                try:
                    asyncio.run(connect(address))
                except Exception:
                    continue
                self.mark([person], self._now(), trigger)

    def _changed(self, trigger: m.Trigger) -> None:
        if not self._paused:
            self._on_change(trigger)

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
            if device:
                self._addresses[person] = device.address
            self.mark([person], self._now(), m.Query.BLE)

    def _now(self) -> datetime:
        return datetime.now(tz=self._tz)


presence = Presence()


def scan_presence(pairs: list[tuple[str, str, str]]) -> tuple[set[str], list[tuple[str, Exception]]]:
    """Return the person names that answered on the LAN, plus any scan failures.

    For each (name, host, mac), resolves host to an IP and probes it. A device
    counts as present if its IP appears as an ARP reply or an mDNS packet during
    the sniff window.
    """
    targets, errors = _resolve_targets(pairs)
    return _probe_lan(targets), errors


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
