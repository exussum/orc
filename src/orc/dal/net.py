import asyncio
import logging
import socket
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, tzinfo
from typing import NamedTuple

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
_PROBE_SECONDS = 30
_BLE_INDEX_REFRESH_SECONDS = 300
_BLE_SEARCH_WINDOWS = 200
_BLE_CLOCK_TRUST_SECONDS = 3 * FMDN_ROTATION_SECONDS

_log = logging.getLogger(__name__)


class Presence:
    """Last-heard time per person, fed by BLE hearings, LAN scan results, and
    manual check-ins; a pause narrows presence to evidence heard after it.

    start() configures the FMDN listener and listen() is its body, run as an
    orc-queue thread service: a tag can't be solicited like the LAN probes'
    targets - it only broadcasts (every ~6s, with multi-cycle dropouts measured
    up to 42s), so windowed scans miss it; the listener hears every frame
    instead. Every frame is one dict lookup against the EID index, rebuilt as
    the 1024s rotation windows advance. The service restarts a crashed scanner
    and gives up after three fast failures, which brings the process down.
    """

    def __init__(self) -> None:
        self.tagged: frozenset[str] = frozenset()
        self._tags: dict[str, m.BleKey] = {}
        self._tz: tzinfo | None = None
        self._index: dict[bytes, tuple[str, int]] = {}
        self._clocks: LockedDict[str, m.TagClock] = LockedDict()
        self._heard: LockedDict[str, datetime] = LockedDict()
        self._frames: dict[str, datetime] = {}
        self._frame_seen = threading.Condition()
        self._paused: datetime | None = None
        self._on_change: Callable[[m.Trigger], object] = lambda trigger: None
        self._on_clock: Callable[[str, m.TagClock], object]

    def start(
        self,
        tags: Iterable[m.BleTag],
        tz: tzinfo,
        on_change: Callable[[m.Trigger], object],
        clocks: Mapping[str, m.TagClock],
        on_clock: Callable[[str, m.TagClock], object],
    ) -> None:
        self._on_change = on_change
        self._on_clock = on_clock
        self.tagged = frozenset(tag.person for tag in tags)
        if not self.tagged:
            return
        self._tz = tz
        self._clocks = LockedDict(clocks)

    def listen(self, stop: threading.Event) -> None:
        while not stop.is_set():
            asyncio.run(self._run(stop))

    def set_key(self, person: str, key: m.BleKey) -> None:
        self._clocks.pop(person)
        self._tags[person] = key

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

    def set_present(self, names: Iterable[str], when: datetime, cutoff: datetime, trigger: m.Trigger) -> set[str]:
        found = set(names)
        for name, heard in self._heard.copy().items():
            if name not in found and heard < cutoff:
                self._heard.pop(name)
        for name in found:
            self._heard[name] = when
        self._changed(trigger)
        return self.present(cutoff)

    def pause(self, until: datetime) -> None:
        self._paused = until

    def resume(self, trigger: m.Trigger) -> None:
        self._paused = None
        self._changed(trigger)

    def probe(self, names: Iterable[str], trigger: m.Trigger) -> None:
        wanted = set(names)
        start = self._now()
        never = datetime.min.replace(tzinfo=start.tzinfo)
        with self._frame_seen:
            self._frame_seen.wait_for(
                lambda: all(self._frames.get(name, never) >= start for name in wanted),
                timeout=_PROBE_SECONDS,
            )
            heard = {name for name in wanted if self._frames.get(name, never) >= start}
        self.mark(heard, self._now(), trigger)

    def _changed(self, trigger: m.Trigger) -> None:
        if not self._paused:
            self._on_change(trigger)

    async def _run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self._index = _eid_index(self._tags, self._clocks.copy(), self._now())
            async with BleakScanner(self._seen, bluez={"filters": {"DuplicateData": True}}):
                await _wait(stop, _BLE_INDEX_REFRESH_SECONDS)

    def _seen(self, device: BLEDevice, data: AdvertisementData) -> None:
        frame = data.service_data.get(FMDN_SERVICE_UUID)
        eid = fmdn_parse(frame) if frame else None
        if eid and (hit := self._index.get(eid)):
            person, window = hit
            now = self._now()
            known = self._clocks.get(person)
            if not known or known.eid != eid:
                clock = m.TagClock(window - (int(now.timestamp()) - self._tags[person].anchor), int(now.timestamp()), eid)
                self._clocks[person] = clock
                if known and known.eid:
                    self._on_clock(person, clock)
            with self._frame_seen:
                self._frames[person] = now
                self._frame_seen.notify_all()
            if not self._paused:
                self.mark([person], now, m.Query("ble"))

    def _now(self) -> datetime:
        return datetime.now(tz=self._tz)


presence = Presence()


class _Target(NamedTuple):
    name: str
    ip: str
    mac: str


def scan_presence(people: Iterable[m.Person]) -> tuple[set[str], list[tuple[str, Exception]]]:
    """Return the person names that answered on the LAN, plus any scan failures.

    For each person, resolves the host to an IP and probes it. A device
    counts as present if its IP appears as an ARP reply or an mDNS packet during
    the sniff window.
    """
    targets, errors = _resolve_targets(people)
    return _probe_lan(targets), errors


def _resolve_targets(
    people: Iterable[m.Person],
) -> tuple[list[_Target], list[tuple[str, Exception]]]:
    """Resolve each person's host to an IP, concurrently.

    gethostbyname blocks on the OS resolver (~5s per unanswered name), so run the
    lookups in parallel: N slow resolutions collapse to one resolver timeout of
    wall-clock instead of summing. Threads run only stdlib DNS, never scapy.
    Returns ([target], [(name, resolution error)]).
    """

    def resolve(pair: tuple[str, m.Host]) -> tuple[str, str, str | Exception]:
        name, host = pair
        try:
            return name, host.mac, socket.gethostbyname(host.host)
        except Exception as exc:
            return name, host.mac, exc

    pairs = [(person.name, host) for person in people for host in person.hosts]
    targets: list[_Target] = []
    errors: list[tuple[str, Exception]] = []
    with ThreadPoolExecutor(max_workers=max(1, len(pairs))) as pool:
        for name, mac, res in pool.map(resolve, pairs):
            if isinstance(res, Exception):
                errors.append((name, res))
            else:
                targets.append(_Target(name, res, mac))
    return targets, errors


def _probe_lan(targets: list[_Target]) -> set[str]:
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
    probes: list = [Ether(dst=t.mac) / ARP(pdst=t.ip, hwdst=t.mac) for t in targets]
    probes.append(
        Ether(dst="01:00:5e:00:00:fb")
        / IP(dst="224.0.0.251", ttl=255)
        / UDP(sport=5353, dport=5353)
        / DNS(rd=0, qd=DNSQR(qname="_services._dns-sd._udp.local", qtype="PTR"))
    )
    sniffer = AsyncSniffer(filter="arp or udp port 5353", store=True)
    sniffer.start()
    # Burst the whole probe list at once, repeat once a second across the window.
    # sendp's `inter` spaces *every* packet, so it would scale with the target
    # count; a manual loop keeps the window fixed regardless of how many devices
    # we track.
    for _ in range(_WINDOW_SECONDS):
        sendp(probes, verbose=False)
        time.sleep(1)
    sniffer.stop()
    responded: set[str] = set()
    for p in sniffer.results or []:
        if ARP in p and p[ARP].op == 2:
            responded.add(p[ARP].psrc)
        elif IP in p:
            responded.add(p[IP].src)
    return {t.name for t in targets if t.ip in responded}


async def _wait(stop: threading.Event, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while not stop.is_set() and time.monotonic() < deadline:
        await asyncio.sleep(1)


def _eid_index(tags: Mapping[str, m.BleKey], clocks: Mapping[str, m.TagClock], now: datetime) -> dict[bytes, tuple[str, int]]:
    ts = int(now.timestamp())
    index: dict[bytes, tuple[str, int]] = {}
    for person, key in dict(tags).items():
        expected = ts - key.anchor
        if (clock := clocks.get(person)) and ts - clock.heard < _BLE_CLOCK_TRUST_SECONDS:
            expected += clock.offset
            span = 1
        else:
            span = _BLE_SEARCH_WINDOWS
        for step in range(-span, span + 1):
            window = (expected + step * FMDN_ROTATION_SECONDS) & ~(FMDN_ROTATION_SECONDS - 1)
            for eid in fmdn_eids(key.eik, window):
                index[eid] = (person, window)
    return index
