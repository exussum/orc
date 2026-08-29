import socket
import time
from concurrent.futures import ThreadPoolExecutor

from scapy.layers.dns import DNS, DNSQR
from scapy.layers.inet import IP, UDP
from scapy.layers.l2 import ARP, Ether
from scapy.sendrecv import AsyncSniffer, sendp


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
    sniffer = AsyncSniffer(filter="arp or udp port 5353", store=True, timeout=3)
    sniffer.start()
    # Burst the whole probe list at once, repeat 3x a second apart. sendp's `inter`
    # spaces *every* packet, so it would scale with the target count; a manual loop
    # keeps the window fixed regardless of how many devices we track.
    for _ in range(3):
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


def scan_presence(pairs: list[tuple[str, str, str]]) -> tuple[set[str], list[tuple[str, Exception]]]:
    """Return the person names reachable on the LAN, plus any hostname-resolution failures.

    For each (name, host, mac), resolves host to an IP and probes it. A device counts as
    present if its IP appears as an ARP reply or an mDNS packet during the sniff window.
    """
    targets, errors = _resolve_targets(pairs)
    return _probe_lan(targets), errors
