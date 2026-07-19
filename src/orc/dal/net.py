import socket

from scapy.layers.dns import DNS, DNSQR
from scapy.layers.inet import IP, UDP
from scapy.layers.l2 import ARP, Ether
from scapy.sendrecv import AsyncSniffer, sendp


def scan_presence(pairs: list[tuple[str, str, str]]) -> tuple[set[str], list[tuple[str, Exception]]]:
    """Return the person names reachable on the LAN, plus any hostname-resolution failures.

    For each (name, host, mac), resolves host to an IP and probes it. A device counts as
    present if its IP appears as an ARP reply or an mDNS packet during the sniff window.
    """
    targets: dict[str, str] = {}  # resolved IP -> person name
    macs: dict[str, str] = {}  # resolved IP -> MAC
    errors: list[tuple[str, Exception]] = []
    for name, host, mac in pairs:
        try:
            ip = socket.gethostbyname(host)
        except Exception as exc:
            errors.append((name, exc))
            continue
        targets[ip], macs[ip] = name, mac
    if not targets:
        return set(), errors
    # Unicast the who-has at each device's MAC (a unicast frame makes the AP wake a
    # Wi-Fi power-save phone that ignores broadcast ARP), plus a multicast mDNS query:
    # a power-save iPhone that ignores ARP will often still answer Bonjour. Sniff both
    # passively, so any ARP or mDNS the device emits also counts. Re-send once a second
    # across the window: a single probe or reply is easily dropped, so repeat.
    probes: list = [Ether(dst=macs[ip]) / ARP(pdst=ip, hwdst=macs[ip]) for ip in targets]
    probes.append(
        Ether(dst="01:00:5e:00:00:fb")
        / IP(dst="224.0.0.251")
        / UDP(sport=5353, dport=5353)
        / DNS(rd=0, qd=DNSQR(qname="_services._dns-sd._udp.local", qtype="PTR"))
    )
    sniffer = AsyncSniffer(filter="arp or udp port 5353", store=True, timeout=3)
    sniffer.start()
    sendp(probes, inter=1, count=3, verbose=False)
    sniffer.join()
    responded: set[str] = set()
    for p in sniffer.results or []:
        if ARP in p and p[ARP].op == 2:
            responded.add(p[ARP].psrc)
        elif IP in p:
            responded.add(p[IP].src)
    return {name for ip, name in targets.items() if ip in responded}, errors
