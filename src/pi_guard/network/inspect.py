"""Find out more about a device in the home network: on request, from this Pi.

Everything here is an estimate from what a device shows to the network: the TTL of a ping
answer, which common ports are open, its manufacturer and name. Only ports a normal
connection attempt reaches are checked, one attempt each.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import socket
from dataclasses import asdict, dataclass, field
from typing import Any

PORTS = {
    22: "SSH", 53: "DNS", 80: "HTTP", 139: "NetBIOS", 443: "HTTPS", 445: "SMB", 515: "LPD",
    548: "AFP", 554: "RTSP", 631: "IPP", 1883: "MQTT", 3389: "RDP", 5000: "UPnP/Synology",
    5353: "mDNS", 5900: "VNC", 7000: "AirPlay", 8008: "Chromecast", 8080: "HTTP",
    8123: "Home Assistant", 8443: "HTTPS", 9100: "Printer", 32400: "Plex", 49000: "FRITZ!Box",
    62078: "iPhone sync",
}  # fmt: skip
CONNECT_TIMEOUT = 1.5
_VENDOR_HINTS = (
    ("apple", "Apple"), ("samsung", "Samsung"), ("xiaomi", "Xiaomi"), ("huawei", "Huawei"),
    ("google", "Google"), ("amazon", "Amazon"), ("sonos", "Sonos"), ("espressif", "ESP32/ESP8266"),
    ("raspberry", "Raspberry Pi"), ("avm", "AVM FRITZ!"), ("synology", "Synology"),
    ("tp-link", "TP-Link"), ("intel", "PC or laptop"), ("dell", "PC or laptop"),
    ("hewlett", "HP"), ("brother", "Printer"), ("canon", "Printer"), ("epson", "Printer"),
)  # fmt: skip


@dataclass
class DeviceReport:
    ip: str
    name: str | None = None
    ttl: int | None = None
    latency_ms: float | None = None
    ports: list[dict[str, Any]] = field(default_factory=list)
    web_title: str | None = None
    web_server: str | None = None
    system: str = "unknown"
    kind: str = "unknown"
    private_mac: bool = False
    maker: str | None = None


def guess(ttl: int | None, ports: set[int], vendor: str | None, mac: str | None) -> dict[str, Any]:
    """Operating system family and kind of device; translation keys, not claims."""
    text = (vendor or "").casefold()
    maker = next((label for hint, label in _VENDOR_HINTS if hint in text), None)
    private = bool(mac) and bool(int((mac or "00")[:2], 16) & 0x02)
    system = "unknown"
    if ttl is not None:
        system = "linux_like" if ttl <= 64 else "windows" if ttl <= 128 else "network_device"
    if 62078 in ports or maker == "Apple":
        system = "apple"
    if 445 in ports and 3389 in ports:
        system = "windows"
    if 548 in ports:
        system = "apple"
    kind = "unknown"
    if 9100 in ports or 631 in ports or 515 in ports or maker == "Printer":
        kind = "printer"
    elif 554 in ports:
        kind = "camera"
    elif 49000 in ports or maker == "AVM FRITZ!":
        kind = "router"
    elif 8008 in ports or 7000 in ports:
        kind = "media"
    elif 62078 in ports or (private and system in ("linux_like", "apple")):
        kind = "phone"
    elif maker == "ESP32/ESP8266":
        kind = "iot"
    elif 3389 in ports or 445 in ports or maker == "PC or laptop":
        kind = "computer"
    return {"system": system, "kind": kind, "private_mac": private, "maker": maker}


async def _ping(ip: str) -> tuple[int | None, float | None]:
    process = await asyncio.create_subprocess_exec(
        "ping", "-n", "-c", "1", "-W", "2", "--", ip,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )  # fmt: skip
    out, _ = await asyncio.wait_for(process.communicate(), 6)
    text = out.decode(errors="replace")
    ttl = re.search(r"ttl=(\d+)", text)
    latency = re.search(r"time=([\d.]+)", text)
    return (int(ttl.group(1)) if ttl else None, float(latency.group(1)) if latency else None)


async def _open(ip: str, port: int) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), CONNECT_TIMEOUT)
    except (OSError, TimeoutError):
        return False
    writer.close()
    return True


async def _web_info(ip: str, port: int) -> tuple[str | None, str | None]:
    """Title and server name of a web page, to recognise routers, NAS and the like."""
    import httpx

    scheme = "https" if port in (443, 8443) else "http"
    try:
        async with httpx.AsyncClient(timeout=3, verify=False) as client:  # noqa: S501 - own LAN
            response = await client.get(f"{scheme}://{ip}:{port}/")
    except httpx.HTTPError:
        return None, None
    match = re.search(r"<title[^>]*>([^<]{1,80})", response.text, re.IGNORECASE)
    server = response.headers.get("server", "")[:60] or None
    return (match.group(1).strip() if match else None), server


async def inspect_device(ip: str, mac: str | None, vendor: str | None) -> dict[str, Any]:
    report = DeviceReport(ip)
    results = await asyncio.gather(*(_open(ip, port) for port in PORTS))
    found = {port for port, is_open in zip(PORTS, results, strict=True) if is_open}
    report.ports = [{"port": p, "service": PORTS[p]} for p in sorted(found)]
    with contextlib.suppress(OSError, TimeoutError):
        report.ttl, report.latency_ms = await _ping(ip)
    with contextlib.suppress(OSError):
        name = await asyncio.get_running_loop().run_in_executor(None, socket.gethostbyaddr, ip)
        report.name = name[0]
    for port in (80, 443, 8080, 8443):
        if port in found:
            report.web_title, report.web_server = await _web_info(ip, port)
            if report.web_title or report.web_server:
                break
    for key, value in guess(report.ttl, found, vendor, mac).items():
        setattr(report, key, value)
    return asdict(report)
