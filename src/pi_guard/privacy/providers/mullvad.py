"""Mullvad VPN as an upstream exit via their public account/device API."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp

from pi_guard.network.wgpeers import generate_keypair
from pi_guard.privacy.errors import PrivacyError
from pi_guard.privacy.providers.base import ExitPeer
from pi_guard.security.files import atomic_write

log = logging.getLogger(__name__)

AUTH_URL = "https://api.mullvad.net/auth/v1/token"
DEVICES_URL = "https://api.mullvad.net/accounts/v1/devices"
ACCOUNT_URL = "https://api.mullvad.net/accounts/v1/accounts/me"
RELAYS_URL = "https://api.mullvad.net/public/relays/wireguard/v1"
ACCOUNT_RE = re.compile(r"^\d{16}$")
MULLVAD_DNS = "10.64.0.1"


class MullvadProvider:
    id = "mullvad"

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        self._session = session

    def capabilities(self) -> dict[str, bool]:
        return {
            "account": True,
            "auto_provision": True,
            "multihop_native": True,
            "quantum_app_only": False,  # not available via standalone WG
            "daita_app_only": False,
        }

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    @staticmethod
    def normalize_account(value: str) -> str:
        digits = re.sub(r"\D", "", value or "")
        if not ACCOUNT_RE.match(digits):
            raise PrivacyError("web.priv_mullvad_account")
        return digits

    @staticmethod
    async def _code(resp: Any) -> str:
        """Mullvad's error code, like INVALID_ACCOUNT; never the account itself."""
        try:
            data = await resp.json(content_type=None)
        except (aiohttp.ContentTypeError, json.JSONDecodeError, ValueError):
            return ""
        return str(data.get("code") or "") if isinstance(data, dict) else ""

    async def token(self, account: str) -> str:
        session = await self._http()
        async with session.post(AUTH_URL, json={"account_number": account}) as resp:
            if resp.status >= 400:
                # A wrong number is answered with 400 INVALID_ACCOUNT (checked 2026-11-12).
                code = await self._code(resp)
                if resp.status in {400, 401, 404} and code in {"", "INVALID_ACCOUNT"}:
                    raise PrivacyError("web.priv_mullvad_bad_account")
                log.warning("mullvad auth failed status=%s code=%s", resp.status, code)
                raise PrivacyError("web.priv_mullvad_api")
            data = await resp.json(content_type=None)
        token = data.get("access_token")
        if not token:
            raise PrivacyError("web.priv_mullvad_api")
        return str(token)

    async def expiry(self, token: str) -> datetime | None:
        """When the paid time ends; None when Mullvad does not say."""
        session = await self._http()
        async with session.get(ACCOUNT_URL, headers={"Authorization": f"Bearer {token}"}) as resp:
            if resp.status >= 400:
                return None
            data = await resp.json(content_type=None)
        try:
            return datetime.fromisoformat(str(data.get("expiry")).replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            return None

    async def account_status(self, account: str) -> dict[str, Any]:
        """Validate the account by obtaining a token and listing devices."""
        token = await self.token(account)
        devices = await self.list_devices(token)
        ends = await self.expiry(token)
        active = ends is None or ends > datetime.now(UTC)
        expiry = ends.isoformat() if ends else None
        return {"active": active, "expiry": expiry, "devices": len(devices)}

    async def list_devices(self, token: str) -> list[dict[str, Any]]:
        session = await self._http()
        async with session.get(DEVICES_URL, headers={"Authorization": f"Bearer {token}"}) as resp:
            if resp.status >= 400:
                raise PrivacyError("web.priv_mullvad_api")
            data = await resp.json(content_type=None)
        return list(data) if isinstance(data, list) else []

    async def register_device(self, account: str, store: Any) -> dict[str, Any]:
        account = self.normalize_account(account)
        token = await self.token(account)
        ends = await self.expiry(token)
        if ends is not None and ends <= datetime.now(UTC):
            # Mullvad still accepts the device, but a tunnel without paid time carries nothing.
            raise PrivacyError("web.priv_mullvad_expired")
        private, public = generate_keypair()
        session = await self._http()
        async with session.post(
            DEVICES_URL,
            headers={"Authorization": f"Bearer {token}"},
            json={"pubkey": public, "hijack_dns": False},
        ) as resp:
            if resp.status >= 400:
                code = await self._code(resp)
                if code == "MAX_DEVICES_REACHED" or (resp.status == 400 and not code):
                    raise PrivacyError("web.priv_mullvad_device_limit")
                log.warning("mullvad device create status=%s code=%s", resp.status, code)
                raise PrivacyError("web.priv_mullvad_api")
            data = await resp.json(content_type=None)
        mullvad = store.data["providers"]["mullvad"]
        mullvad.update(
            {
                "account_sealed": store.seal(account),
                "device_id": data.get("id"),
                "device_name": data.get("name"),
                "ipv4_address": data.get("ipv4_address"),
                "ipv6_address": data.get("ipv6_address"),
                "public_key": public,
                "private_key_sealed": store.seal(private),
                "expiry": ends.isoformat() if ends else None,
            }
        )
        store.save()
        return {
            "device_id": mullvad["device_id"],
            "device_name": mullvad["device_name"],
            "ipv4_address": mullvad["ipv4_address"],
        }

    async def remove_account(self, store: Any) -> None:
        mullvad = store.data["providers"]["mullvad"]
        account = store.open(mullvad.get("account_sealed"))
        device_id = mullvad.get("device_id")
        if account and device_id:
            try:
                token = await self.token(account)
                session = await self._http()
                async with session.delete(
                    f"{DEVICES_URL}/{device_id}",
                    headers={"Authorization": f"Bearer {token}"},
                ) as resp:
                    if resp.status not in {204, 404}:
                        log.warning("mullvad device delete status=%s", resp.status)
            except PrivacyError:
                log.warning("mullvad cleanup skipped: account no longer valid")
        store.data["providers"]["mullvad"] = {
            "account_sealed": None,
            "device_id": None,
            "device_name": None,
            "ipv4_address": None,
            "ipv6_address": None,
            "public_key": None,
            "private_key_sealed": None,
            "expiry": None,
            "selected": None,
        }
        hops = store.data["chain"]["hops"]
        store.data["chain"]["hops"] = [h for h in hops if h.get("provider") != "mullvad"]
        store.save()

    async def relays(self, cache: Path, *, refresh: bool = False) -> list[dict[str, Any]]:
        if cache.is_file() and not refresh:
            try:
                return list(json.loads(cache.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                pass
        session = await self._http()
        async with session.get(RELAYS_URL) as resp:
            if resp.status >= 400:
                raise PrivacyError("web.priv_mullvad_api")
            raw = await resp.json(content_type=None)
        countries = []
        for country in raw.get("countries") or []:
            cities = []
            for city in country.get("cities") or []:
                relays = []
                for relay in city.get("relays") or []:
                    if not relay.get("active", True):
                        continue
                    relays.append(
                        {
                            "hostname": relay.get("hostname"),
                            "ipv4": relay.get("ipv4_addr_in"),
                            "ipv6": relay.get("ipv6_addr_in"),
                            "public_key": relay.get("pubkey") or relay.get("public_key"),
                            "multihop_port": relay.get("multihop_port"),
                        }
                    )
                if relays:
                    cities.append(
                        {
                            "code": city.get("code"),
                            "name": city.get("name"),
                            "relays": relays,
                        }
                    )
            if cities:
                countries.append(
                    {
                        "code": country.get("code"),
                        "name": country.get("name"),
                        "cities": cities,
                    }
                )
        atomic_write(cache, json.dumps(countries).encode())
        return countries

    @staticmethod
    def find_relay(countries: list[dict[str, Any]], hostname: str) -> dict[str, Any] | None:
        for country in countries:
            for city in country.get("cities") or []:
                for relay in city.get("relays") or []:
                    if relay.get("hostname") == hostname:
                        return {"country": country.get("code"), "city": city.get("code"), **relay}
        return None

    def select_server(
        self, store: Any, selection: dict[str, Any], countries: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """Exit server, and for Mullvad's own multi-hop an entry server.

        With the relay list, keys, addresses and ports come from it, not from the browser.
        """
        mullvad = store.data["providers"]["mullvad"]
        if not mullvad.get("device_id"):
            raise PrivacyError("web.priv_mullvad_need_account")
        hostname = str(selection.get("hostname") or "").strip()
        if not hostname:
            raise PrivacyError("web.priv_mullvad_server")
        entry_name = str(selection.get("entry_hostname") or "").strip() or None
        if entry_name == hostname:
            raise PrivacyError("web.priv_mullvad_same")
        if countries is not None:
            exit_relay = self.find_relay(countries, hostname)
            entry_relay = self.find_relay(countries, entry_name) if entry_name else None
            if exit_relay is None or (entry_name and entry_relay is None):
                raise PrivacyError("web.priv_mullvad_server")
            selection = {**exit_relay, "entry_hostname": entry_name,
                         "entry_ipv4": (entry_relay or {}).get("ipv4"),
                         "entry_public_key": (entry_relay or {}).get("public_key")}  # fmt: skip
        if entry_name and not (selection.get("entry_ipv4") and selection.get("multihop_port")):
            raise PrivacyError("web.priv_mullvad_server")
        mullvad["selected"] = {
            "country": selection.get("country"),
            "city": selection.get("city"),
            "hostname": hostname,
            "entry_hostname": entry_name,
            "public_key": selection.get("public_key"),
            "ipv4": selection.get("ipv4"),
            # Multi-hop: the entry's address with the exit's port and key.
            "multihop_port": selection.get("multihop_port"),
            "entry_ipv4": selection.get("entry_ipv4") if entry_name else None,
            "entry_public_key": selection.get("entry_public_key") if entry_name else None,
        }
        store.save()
        selected = mullvad["selected"]
        assert isinstance(selected, dict)
        return selected

    def build_peer(self, store: Any, hop: dict[str, Any] | None = None) -> ExitPeer:
        mullvad = store.data["providers"]["mullvad"]
        private = store.open(mullvad.get("private_key_sealed"))
        address = mullvad.get("ipv4_address")
        selected = (hop or {}).get("params") or mullvad.get("selected") or {}
        if not private or not address:
            raise PrivacyError("web.priv_mullvad_need_account")
        if not selected.get("hostname") and not selected.get("ipv4"):
            raise PrivacyError("web.priv_mullvad_server")
        # Native Mullvad multi-hop: connect to entry IP:multihop_port with exit server's pubkey.
        # A multi-hop that cannot be built is refused, never quietly made a single hop.
        if selected.get("entry_hostname"):
            if not (selected.get("entry_ipv4") and selected.get("multihop_port")):
                raise PrivacyError("web.priv_mullvad_server")
            endpoint = f"{selected['entry_ipv4']}:{int(selected['multihop_port'])}"
        else:
            # Relay names alone do not resolve (they live under relays.mullvad.net).
            host = selected.get("ipv4")
            if not host:
                raise PrivacyError("web.priv_mullvad_server")
            endpoint = f"{host}:51820" if ":" not in str(host) else str(host)
        peer_key = str(selected.get("public_key") or "")
        if not peer_key:
            raise PrivacyError("web.priv_mullvad_server")
        addr = address if "/" in str(address) else f"{address}/32"
        return ExitPeer(
            interface_address=addr,
            private_key=private,
            peer_public_key=peer_key,
            endpoint=endpoint,
            allowed_ips="0.0.0.0/0, ::/0",
            dns=MULLVAD_DNS,
            mtu=1280,
        )
