from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from pi_guard.config.files import TrustedDevice
from pi_guard.network.lan import merge
from pi_guard.network.pihole import Pihole, PiholeDevice, PiholeError
from pi_guard.network.wireguard import parse_status
from pi_guard.presence.resolver import Observation
from pi_guard.presence.sources import PiholeSource

PASSWORD = "app-password"


class FakePihole:
    """Speaks the parts of the Pi-hole v6 API pi-guard uses."""

    def __init__(self) -> None:
        self.sids: set[str] = set()
        self.logins = 0
        self.blocking: dict[str, Any] = {"blocking": "enabled", "timer": None}
        now = time.time()
        self.devices = [
            {"hwaddr": "aa:bb:cc:dd:ee:01", "macVendor": "Apple, Inc.", "lastQuery": now - 30,
             "numQueries": 812, "ips": [{"ip": "192.168.178.40", "name": "annas-iphone"}]},
            {"hwaddr": "aa:bb:cc:dd:ee:02", "macVendor": "", "lastQuery": now - 7200,
             "numQueries": 3, "ips": [{"ip": "192.168.178.41", "name": None}]},
            {"hwaddr": "ip-192.168.178.99", "lastQuery": now, "numQueries": 1, "ips": []},
        ]  # fmt: skip

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/api/auth", self.auth)
        app.router.add_delete("/api/auth", self.ok)
        app.router.add_get("/api/stats/summary", self.summary)
        app.router.add_get("/api/dns/blocking", self.get_blocking)
        app.router.add_post("/api/dns/blocking", self.set_blocking)
        app.router.add_get("/api/stats/top_domains", self.top)
        app.router.add_get("/api/network/devices", self.network)
        return app

    def _check(self, request: web.Request) -> None:
        if request.headers.get("X-FTL-SID") not in self.sids:
            raise web.HTTPUnauthorized()

    async def auth(self, request: web.Request) -> web.Response:
        body = await request.json()
        if body.get("password") != PASSWORD:
            return web.json_response({"session": {"valid": False}}, status=401)
        self.logins += 1
        sid = f"sid{self.logins}"
        self.sids.add(sid)
        return web.json_response({"session": {"valid": True, "sid": sid, "validity": 1800}})

    async def ok(self, request: web.Request) -> web.Response:
        return web.json_response({})

    async def summary(self, request: web.Request) -> web.Response:
        self._check(request)
        return web.json_response({
            "queries": {"total": 1000, "blocked": 123, "percent_blocked": 12.3456},
            "clients": {"active": 7}, "gravity": {"domains_being_blocked": 150000},
        })  # fmt: skip

    async def get_blocking(self, request: web.Request) -> web.Response:
        self._check(request)
        return web.json_response(self.blocking)

    async def set_blocking(self, request: web.Request) -> web.Response:
        self._check(request)
        body = await request.json()
        self.blocking = {"blocking": "enabled" if body["blocking"] else "disabled",
                         "timer": body.get("timer")}  # fmt: skip
        return web.json_response(self.blocking)

    async def top(self, request: web.Request) -> web.Response:
        self._check(request)
        assert request.query["blocked"] == "true"
        return web.json_response({"domains": [{"domain": "ads.example", "count": 42}]})

    async def network(self, request: web.Request) -> web.Response:
        self._check(request)
        return web.json_response({"devices": self.devices})


@pytest.fixture
async def pihole():  # type: ignore[no-untyped-def]
    fake = FakePihole()
    server = TestServer(fake.app())
    await server.start_server()
    client = Pihole(str(server.make_url("")), PASSWORD)
    yield fake, client
    await client.close()
    await server.close()


async def test_pihole_summary_and_blocking(pihole) -> None:  # type: ignore[no-untyped-def]
    fake, client = pihole
    summary = await client.summary()
    assert summary["percent_blocked"] == 12.3 and summary["blocking"] is True
    assert summary["domains_blocked"] == 150000 and summary["clients"] == 7
    await client.set_blocking(False, minutes=5)
    assert fake.blocking == {"blocking": "disabled", "timer": 300}
    assert (await client.top_blocked())[0]["domain"] == "ads.example"


async def test_pihole_logs_in_again_after_expiry(pihole) -> None:  # type: ignore[no-untyped-def]
    fake, client = pihole
    await client.summary()
    fake.sids.clear()  # session expired on the Pi-hole side
    await client.summary()
    assert fake.logins == 2


async def test_pihole_wrong_password() -> None:
    fake = FakePihole()
    server = TestServer(fake.app())
    await server.start_server()
    client = Pihole(str(server.make_url("")), "wrong")
    with pytest.raises(PiholeError, match="password"):
        await client.summary()
    await client.close()
    await server.close()


async def test_pihole_unreachable() -> None:
    client = Pihole("http://127.0.0.1:9", PASSWORD)
    with pytest.raises(PiholeError, match="not reachable"):
        await client.request("GET", "/api/stats/summary")
    await client.close()


async def test_pihole_devices_skip_placeholders(pihole) -> None:  # type: ignore[no-untyped-def]
    _, client = pihole
    devices = await client.devices()
    assert [d.mac for d in devices] == ["aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"]
    assert devices[0].name == "annas-iphone" and devices[0].vendor == "Apple, Inc."
    assert devices[1].vendor is None


async def test_pihole_presence_source(pihole) -> None:  # type: ignore[no-untyped-def]
    _, client = pihole
    trusted = [TrustedDevice(id="anna", name="Anna", mac_address="aa:bb:cc:dd:ee:01"),
               TrustedDevice(id="old", name="Old", mac_address="aa:bb:cc:dd:ee:02"),
               TrustedDevice(id="gone", name="Gone", mac_address="aa:bb:cc:dd:ee:03")]  # fmt: skip
    result = await PiholeSource(client).observe(trusted)
    assert result == {"anna": Observation.SEEN, "old": Observation.NOT_SEEN,
                      "gone": Observation.NOT_SEEN}  # fmt: skip
    broken = PiholeSource(Pihole("http://127.0.0.1:9", PASSWORD))
    assert (await broken.observe(trusted))["anna"] is Observation.ERROR


def test_lan_merge_marks_trusted_and_random_macs() -> None:
    now = datetime.now(UTC)
    neighbours = {"a4:83:e7:00:00:01": ("192.168.178.40", {"REACHABLE"}),
                  "3a:11:22:33:44:55": ("192.168.178.77", {"STALE"})}  # fmt: skip
    pihole = [PiholeDevice("a4:83:e7:00:00:01", ("192.168.178.40",), "annas-iphone", "Apple",
                           now - timedelta(minutes=1), 10)]  # fmt: skip
    devices = merge(neighbours, pihole, {"a4:83:e7:00:00:01": "Annas Handy"},
                    {"192.168.178.77": "tv.fritz.box"})  # fmt: skip
    assert devices[0].trusted == "Annas Handy" and devices[0].vendor == "Apple"
    assert devices[1].name == "tv.fritz.box" and devices[1].randomized
    assert not devices[0].randomized


def test_wireguard_status_parsing() -> None:
    now = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
    recent = int((now - timedelta(seconds=60)).timestamp())
    text = (
        "written\t2026-11-02T12:00:00Z\n"
        "name\thandy\tPUBKEY1=\n"
        "(hidden)\tSERVERPUB=\t51820\toff\n"
        f"PUBKEY1=\t(hidden)\t203.0.113.5:4500\t10.6.0.2/32\t{recent}\t1048576\t2097152\t25\n"
        "PUBKEY2=\t(hidden)\t(none)\t10.6.0.3/32\t0\t0\t0\toff\n"
    )
    status = parse_status(text, now)
    assert status["interface"]["listen_port"] == "51820"
    first, second = status["peers"]
    assert first["name"] == "handy" and first["active"] and first["received"] == 1048576
    assert second["name"] is None and not second["active"] and second["endpoint"] is None
    assert "(hidden)" not in str(status["peers"])


async def test_trust_device_writes_presence_config(tmp_path: Path) -> None:
    from aiohttp.test_utils import TestClient

    from pi_guard.web import auth
    from pi_guard.web.server import Dashboard

    from .helpers import make_app

    app = make_app(tmp_path, dashboard_enabled=True, dashboard_tls=False)
    dashboard = Dashboard(app)
    dashboard.auth.change_credentials(auth.DEFAULT_PASSWORD, "admin", "pw-1234567890")
    client = TestClient(TestServer(dashboard.web))
    await client.start_server()
    await client.post("/api/login", json={"username": "admin", "password": "pw-1234567890"})
    csrf = (await (await client.get("/api/session")).json())["csrf"]
    headers = {"X-CSRF-Token": csrf}
    bad = await client.post(
        "/api/network/trust", json={"mac": "nope", "name": "x"}, headers=headers
    )
    assert bad.status == 400
    ok = await client.post(
        "/api/network/trust",
        json={"mac": "AA:BB:CC:DD:EE:01", "name": "Annas Handy"},
        headers=headers,
    )
    assert ok.status == 200
    written = (app.settings.data_dir / "dashboard.yaml").read_text()
    assert "aa:bb:cc:dd:ee:01" in written and "annas_handy" in written
    network = await (await client.get("/api/network")).json()
    assert network["pihole_enabled"] is False and isinstance(network["devices"], list)
    await client.close()
    app.db.close()
