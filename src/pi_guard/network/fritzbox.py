"""Read-only access to a FRITZ!Box over TR-064: Wi-Fi networks and its device list.

The box knows names people recognise ("Galaxy-S23-von-Max"), which device is on which Wi-Fi,
and whether the guest Wi-Fi is on. Nothing here changes anything on the router.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any
from xml.etree import ElementTree as ET

import httpx

TIMEOUT = 8
PORT = 49000
WLAN_LABELS = {1: "2.4 GHz", 2: "5 GHz", 3: "guest"}
MAX_HOSTS = 200


class FritzError(Exception):
    pass


@dataclass(frozen=True)
class Wlan:
    index: int
    ssid: str
    enabled: bool
    channel: int
    standard: str
    clients: int
    guest: bool


@dataclass(frozen=True)
class FritzHost:
    mac: str
    ip: str
    name: str
    active: bool
    interface: str
    speed: int


def _value(xml: str, tag: str) -> str:
    match = re.search(rf"<{tag}>([^<]*)</{tag}>", xml)
    return match.group(1) if match else ""


class FritzBox:
    def __init__(self, host: str, username: str, password: str) -> None:
        self._base = f"http://{host}:{PORT}"
        self._host = host
        self._username = username
        self._password = password

    async def _user(self) -> str:
        """The box names its last user without login; needed when none is configured."""
        if self._username:
            return self._username
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(f"http://{self._host}/login_sid.lua?version=2")
        match = re.search(r'<User last="1">([^<]+)</User>', response.text)
        if match is None:
            raise FritzError("no user name")
        self._username = match.group(1)
        return self._username

    async def _call(self, service: str, path: str, action: str) -> str:
        body = (
            '<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action} xmlns:u="{service}"/></s:Body></s:Envelope>'
        )
        headers = {"Content-Type": 'text/xml; charset="utf-8"', "SoapAction": f"{service}#{action}"}
        try:
            auth = httpx.DigestAuth(await self._user(), self._password)
            async with httpx.AsyncClient(timeout=TIMEOUT, auth=auth) as client:
                response = await client.post(f"{self._base}{path}", content=body, headers=headers)
        except httpx.HTTPError as exc:
            raise FritzError(type(exc).__name__) from exc
        if response.status_code == 401:
            raise FritzError("login refused")
        if response.status_code >= 400:
            raise FritzError(f"answered {response.status_code}")
        return response.text

    async def wlans(self) -> list[Wlan]:
        result = []
        for index in WLAN_LABELS:
            service = f"urn:dslforum-org:service:WLANConfiguration:{index}"
            try:
                info = await self._call(service, f"/upnp/control/wlanconfig{index}", "GetInfo")
                total = await self._call(service, f"/upnp/control/wlanconfig{index}",
                                         "GetTotalAssociations")  # fmt: skip
            except FritzError:
                continue
            ssid = _value(info, "NewSSID")
            clients = int(_value(total, "NewTotalAssociations") or 0)
            result.append(Wlan(index, ssid, _value(info, "NewEnable") == "1",
                               int(_value(info, "NewChannel") or 0), _value(info, "NewStandard"),
                               clients, index == 3))  # fmt: skip
        return result

    async def hosts(self) -> list[FritzHost]:
        service = "urn:dslforum-org:service:Hosts:1"
        answer = await self._call(service, "/upnp/control/hosts", "X_AVM-DE_GetHostListPath")
        path = _value(answer, "NewX_AVM-DE_HostListPath")
        if not path:
            raise FritzError("no host list")
        try:
            auth = httpx.DigestAuth(await self._user(), self._password)
            async with httpx.AsyncClient(timeout=TIMEOUT, auth=auth) as client:
                response = await client.get(f"{self._base}{path}")
        except httpx.HTTPError as exc:
            raise FritzError(type(exc).__name__) from exc
        hosts = []
        for item in ET.fromstring(response.text).iter("Item"):  # noqa: S314 - own router

            def text(tag: str, node: Any = item) -> str:
                return (node.findtext(tag) or "").strip()

            mac = text("MACAddress").lower()
            if mac:
                hosts.append(FritzHost(mac, text("IPAddress"), text("HostName"),
                                       text("Active") == "1", text("InterfaceType"),
                                       int(text("X_AVM-DE_Speed") or 0)))  # fmt: skip
        return hosts[:MAX_HOSTS]


def as_dicts(items: list[Any]) -> list[dict[str, Any]]:
    return [asdict(i) for i in items]
