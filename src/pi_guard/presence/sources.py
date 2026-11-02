"""Where presence observations come from.

`NeighbourSource` uses the kernel's neighbour (ARP) table. Before reading it,
a single UDP datagram is sent to each trusted device's last known address;
this makes the kernel re-verify the entry with ARP, which phones usually
answer even when they ignore ping. Nothing else on the network is scanned.

`FritzBoxSource` asks an AVM FRITZ!Box via TR-064 whether a MAC address is an
active client, which is more reliable than ARP for sleeping phones.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import socket
from datetime import UTC, datetime
from typing import Protocol
from xml.etree import ElementTree

import httpx

from pi_guard.config.files import FritzBoxSettings, TrustedDevice
from pi_guard.network.pihole import Pihole, PiholeError
from pi_guard.presence.resolver import Observation

log = logging.getLogger(__name__)

_REACHABLE = {"REACHABLE"}
_PROBE_WAIT_SECONDS = 2.0


class PresenceSource(Protocol):
    name: str

    async def observe(self, devices: list[TrustedDevice]) -> dict[str, Observation]: ...


def parse_neighbours(output: str) -> dict[str, tuple[str, set[str]]]:
    """`ip -j neigh` JSON -> {mac: (ip, states)}; the newest REACHABLE entry wins."""
    table: dict[str, tuple[str, set[str]]] = {}
    for entry in json.loads(output or "[]"):
        mac = str(entry.get("lladdr", "")).lower()
        if not mac:
            continue
        states = set(entry.get("state", []))
        current = table.get(mac)
        if current is None or (states & _REACHABLE and not current[1] & _REACHABLE):
            table[mac] = (entry["dst"], states)
    return table


class NeighbourSource:
    name = "neighbours"

    def __init__(self, interface: str | None = None) -> None:
        self._interface = interface
        self._known_ip: dict[str, str] = {}

    async def _table(self) -> dict[str, tuple[str, set[str]]]:
        args = ["ip", "-j", "neigh", "show"]
        if self._interface:
            args += ["dev", self._interface]
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
        if process.returncode != 0:
            raise RuntimeError(stderr.decode(errors="replace").strip())
        return parse_neighbours(stdout.decode())

    @staticmethod
    def _poke(ip: str) -> None:
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        with contextlib.suppress(OSError), socket.socket(family, socket.SOCK_DGRAM) as sock:
            sock.sendto(b"", (ip, 9))  # discard port; only the ARP lookup matters

    async def observe(self, devices: list[TrustedDevice]) -> dict[str, Observation]:
        try:
            table = await self._table()
            for device in devices:
                ip = device.ip_address or self._known_ip.get(device.mac_address)
                if ip is None and device.mac_address in table:
                    ip = table[device.mac_address][0]
                if ip:
                    self._poke(ip)
            await asyncio.sleep(_PROBE_WAIT_SECONDS)
            table = await self._table()
        except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
            log.warning("neighbour table unavailable: %s", exc)
            return {d.id: Observation.ERROR for d in devices}
        result = {}
        for device in devices:
            entry = table.get(device.mac_address)
            if entry is not None:
                self._known_ip[device.mac_address] = entry[0]
            seen = entry is not None and bool(entry[1] & _REACHABLE)
            result[device.id] = Observation.SEEN if seen else Observation.NOT_SEEN
        return result


_SOAP = """<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"
 s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>
<u:GetSpecificHostEntry xmlns:u="urn:dslforum-org:service:Hosts:1">
<NewMACAddress>{mac}</NewMACAddress></u:GetSpecificHostEntry></s:Body></s:Envelope>"""
_MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")


def parse_host_entry(xml: str) -> bool:
    root = ElementTree.fromstring(xml)  # noqa: S314 - response from the configured router
    for element in root.iter():
        if element.tag.endswith("NewActive"):
            return (element.text or "").strip() == "1"
    raise ValueError("NewActive missing in TR-064 response")


class FritzBoxSource:
    name = "fritzbox"

    def __init__(self, settings: FritzBoxSettings, password: str | None) -> None:
        self._url = f"http://{settings.host}:{settings.port}/upnp/control/hosts"
        self._auth = httpx.DigestAuth(settings.username, password or "")

    async def observe(self, devices: list[TrustedDevice]) -> dict[str, Observation]:
        result: dict[str, Observation] = {}
        async with httpx.AsyncClient(auth=self._auth, timeout=10) as client:
            for device in devices:
                mac = device.mac_address.upper()
                if not _MAC_RE.match(mac):
                    result[device.id] = Observation.ERROR
                    continue
                try:
                    response = await client.post(
                        self._url,
                        content=_SOAP.format(mac=mac),
                        headers={
                            "Content-Type": 'text/xml; charset="utf-8"',
                            "SOAPAction": "urn:dslforum-org:service:Hosts:1#GetSpecificHostEntry",
                        },
                    )
                    if response.status_code == 500 and "NoSuchEntryInArray" in response.text:
                        result[device.id] = Observation.NOT_SEEN  # router does not know it
                        continue
                    response.raise_for_status()
                    active = parse_host_entry(response.text)
                    result[device.id] = Observation.SEEN if active else Observation.NOT_SEEN
                except (httpx.HTTPError, ValueError, ElementTree.ParseError) as exc:
                    log.warning("FRITZ!Box query failed: %s", exc)
                    result[device.id] = Observation.ERROR
        return result


class PiholeSource:
    """A device counts as seen when it sent a DNS query to Pi-hole recently.

    Phones keep querying in the background, so this works well when Pi-hole is
    the network's DNS server. It complements the neighbour table.
    """

    name = "pihole"

    def __init__(self, pihole: Pihole, window_seconds: int = 300) -> None:
        self._pihole = pihole
        self._window = window_seconds

    async def observe(self, devices: list[TrustedDevice]) -> dict[str, Observation]:
        try:
            known = {d.mac: d for d in await self._pihole.devices()}
        except PiholeError as exc:
            log.warning("Pi-hole presence check failed: %s", exc)
            return {d.id: Observation.ERROR for d in devices}
        now = datetime.now(UTC)
        result = {}
        for device in devices:
            entry = known.get(device.mac_address)
            recent = (
                entry is not None
                and entry.last_query is not None
                and ((now - entry.last_query).total_seconds() < self._window)
            )
            result[device.id] = Observation.SEEN if recent else Observation.NOT_SEEN
        return result
