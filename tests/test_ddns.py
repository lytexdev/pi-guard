"""Dynamic DNS through Cloudflare: the record follows the address, nothing else is touched."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from pi_guard.config.settings import Settings
from pi_guard.network import ddns
from pi_guard.network.ddns import CloudflareDNS, DDNSError, zone_candidates

from .helpers import make_app

TOKEN = "cf-token-1234567890"


class FakeCloudflare:
    """Just enough of the API: one zone, records by type and name."""

    def __init__(self, token: str = TOKEN) -> None:
        self.token = token
        self.records: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        if request.headers.get("Authorization") != f"Bearer {self.token}":
            return httpx.Response(403, json={"success": False, "errors": [{"message": "no"}]})
        path, params = request.url.path, request.url.params
        if path.endswith("/zones"):
            zone = [{"id": "z1"}] if params.get("name") == "example.org" else []
            return httpx.Response(200, json={"success": True, "result": zone})
        if request.method == "GET":
            wanted = (params.get("type"), params.get("name"))
            found = [r for r in self.records if (r["type"], r["name"]) == wanted]
            return httpx.Response(200, json={"success": True, "result": found})
        body = json.loads(request.content)
        assert body["proxied"] is False  # WireGuard needs the real address
        if request.method == "POST":
            self.records.append({**body, "id": f"r{len(self.records)}"})
        else:
            record = next(r for r in self.records if path.endswith(r["id"]))
            record.update(body)
        return httpx.Response(200, json={"success": True, "result": {}})


def test_the_zone_is_found_from_the_name() -> None:
    assert zone_candidates("a.home.example.org") == ["a.home.example.org", "home.example.org",
                                                     "example.org"]  # fmt: skip


async def test_a_record_is_created_changed_and_left_alone() -> None:
    fake = FakeCloudflare()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)) as client:
        dns = CloudflareDNS(TOKEN, client)
        zone = await dns.zone_id("home.example.org")
        assert zone == "z1"
        assert await dns.set_record(zone, "A", "home.example.org", "203.0.113.5") == "created"
        assert await dns.set_record(zone, "A", "home.example.org", "203.0.113.5") == "same"
        assert await dns.set_record(zone, "A", "home.example.org", "203.0.113.9") == "updated"
        assert fake.records[0]["content"] == "203.0.113.9"
        with pytest.raises(DDNSError, match="no_zone"):
            await dns.zone_id("home.other.net")
        with pytest.raises(DDNSError, match="token_refused") as refused:
            await CloudflareDNS("wrong", client).zone_id("home.example.org")
        assert "wrong" not in str(refused.value) and TOKEN not in str(refused.value)


async def test_the_pi_updates_only_when_the_address_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeCloudflare()
    real = httpx.AsyncClient

    def client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return real(transport=httpx.MockTransport(fake.handler))

    monkeypatch.setattr(ddns.httpx, "AsyncClient", client)
    address = {"ip": "203.0.113.5"}

    async def home_ip() -> str:
        return address["ip"]

    monkeypatch.setattr("pi_guard.web.api_addons.home_ip", home_ip)
    app = make_app(tmp_path, ddns_provider="cloudflare", ddns_hostname="home.example.org",
                   ddns_cloudflare_token=TOKEN)  # fmt: skip
    try:
        assert app.settings.wireguard_endpoint == "home.example.org"  # the VPN uses the name
        status = await app.ddns.update()
        assert status["ok"] and status["a"] == "created"
        asked = len(fake.calls)
        await app.ddns.update()  # nothing changed: Cloudflare is not asked at all
        assert len(fake.calls) == asked
        address["ip"] = "203.0.113.9"
        assert (await app.ddns.update())["a"] == "updated"
        assert fake.records[0]["content"] == "203.0.113.9"
    finally:
        app.db.close()


def test_cloudflare_needs_a_name_and_a_token() -> None:
    base = {"_env_file": None, "telegram_enabled": False, "ddns_provider": "cloudflare"}
    with pytest.raises(ValidationError, match="DDNS"):
        Settings(**base, ddns_hostname="home.example.org")
    with pytest.raises(ValidationError, match=r"home\.example\.org"):
        Settings(**base, ddns_hostname="not a name", ddns_cloudflare_token=TOKEN)
    own = Settings(**base, ddns_hostname="Home.Example.org.", ddns_cloudflare_token=TOKEN,
                   wireguard_endpoint="vpn.example.org:51820")  # fmt: skip
    assert own.ddns_hostname == "home.example.org"
    assert own.wireguard_endpoint == "vpn.example.org:51820"  # an own VPN address stays
