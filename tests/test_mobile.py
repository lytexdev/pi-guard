"""The phone side: pairing, tokens, alerts, finding, emergencies, app updates, calendar."""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from pi_guard.addons.mobile import MobileHub, MobileProvider, clean_report, map_link
from pi_guard.core.modes import Mode, ModeController, Presence
from pi_guard.core.schedule import CALENDAR_KEY, PLAN_KEY, ScheduleRunner, clean_calendar
from pi_guard.messaging.base import ProviderUnavailableError, Reply

from .test_web import PASSWORD, _client, _login

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


async def _paired(tmp_path: Path) -> tuple[Any, Any, dict[str, str], dict[str, str]]:
    client, app = await _client(tmp_path, mobile_enabled=True)
    headers = {"X-CSRF-Token": await _login(client)}
    body = {"name": "Handy Lena", "platform": "android"}
    response = await client.post("/api/mobile/pair", json=body, headers=headers)
    assert response.status == 200
    bearer = {"Authorization": f"Bearer {(await response.json())['token']}"}
    return client, app, headers, bearer


def test_reports_keep_only_known_fields() -> None:
    report = clean_report({"latitude": 52.5, "longitude": 13.4, "battery": 80, "evil": "x",
                           "ssid": "y" * 200, "charging": "yes", "accuracy": True})  # fmt: skip
    assert report == {"latitude": 52.5, "longitude": 13.4, "battery": 80, "ssid": "y" * 80}
    assert "latitude" not in clean_report({"latitude": 500.0, "longitude": 1.0})
    assert map_link({"latitude": 52.5, "longitude": 13.4}).startswith(
        "https://www.openstreetmap.org"
    )
    assert map_link({}) is None


async def test_tokens_are_hashed_and_revocable(db: Any) -> None:
    hub = MobileHub(db)
    device_id, token = hub.create("Handy", "android", "admin")
    assert token not in str(db.execute("SELECT token FROM mobile_devices").fetchone()[0])
    assert hub.authenticate(token)["id"] == device_id and "token" not in hub.get(device_id)
    with pytest.raises(ValueError, match="platform"):
        hub.create("x", "windows", "admin")
    hub.delete(device_id)
    assert hub.authenticate(token) is None


async def test_alert_reaches_the_phone_or_is_retried(db: Any, tmp_path: Path) -> None:
    hub = MobileHub(db)
    device_id, _ = hub.create("Handy", "android", "admin")
    provider = MobileProvider(hub)
    with pytest.raises(ProviderUnavailableError):
        await provider.send(str(device_id), Reply("Alarm"))
    sent: list[str] = []

    async def send(text: str) -> None:
        sent.append(text)

    hub.attach(device_id, send)
    photo = tmp_path / "x.jpg"
    photo.write_bytes(b"jpeg")
    await provider.send(str(device_id), Reply("Alarm", photo=photo))
    assert '"type": "alert"' in sent[0] and '"photo": "anBlZw=="' in sent[0]


async def test_pairing_needs_login_and_the_home_network(tmp_path: Path) -> None:
    client, app = await _client(tmp_path, mobile_enabled=True)
    try:
        body = {"name": "x", "platform": "android"}
        assert (await client.post("/api/mobile/pair", json=body)).status == 401
        assert await (await client.get("/api/hello")).json() == {"app": "pi-guard"}
        headers = {"X-CSRF-Token": await _login(client)}
        app.dashboard.guard.client_ip = lambda request: "8.8.8.8"
        refused = await client.post("/api/mobile/pair", json=body, headers=headers)
        assert refused.status == 403 and await refused.text() == "web.mobile_home_only"
    finally:
        await client.close()
        app.db.close()


async def test_stored_login_and_bad_tokens(tmp_path: Path) -> None:
    client, app, _, bearer = await _paired(tmp_path)
    try:
        wrong = {"Authorization": "Bearer nope"}
        assert (await client.post("/api/mobile/session", headers=wrong)).status == 401
        client.session.cookie_jar.clear()
        assert (await client.post("/api/mobile/session", headers=bearer)).status == 200
        assert (await client.get("/api/overview")).status == 200
        report = {"latitude": 52.5, "longitude": 13.4, "battery": 50}
        assert (await client.post("/api/mobile/report", json=report, headers=bearer)).status == 200
        rows = (await (await client.get("/api/mobile/devices")).json())["devices"]
        assert rows[0]["info"]["battery"] == 50 and "openstreetmap" in rows[0]["map"]
        assert app.users.get("mobile", str(rows[0]["id"])) is not None  # gets alerts
    finally:
        await client.close()
        app.db.close()


async def test_finding_through_the_live_connection(tmp_path: Path) -> None:
    client, app, headers, bearer = await _paired(tmp_path)
    try:
        url = "/api/mobile/devices/1/find"
        offline = await client.post(url, headers=headers)
        assert offline.status == 409
        socket = await client.ws_connect("/api/mobile/ws", headers=bearer)
        assert (await client.post(url, headers=headers)).status == 200
        assert (await socket.receive_json()) == {"type": "find", "ring": True}
        await socket.send_json({"type": "report", "battery": 12})
        await socket.close()
        for _ in range(50):
            if app.mobile.get(1)["info"].get("battery") == 12:
                break
            await __import__("asyncio").sleep(0.01)
        assert app.mobile.get(1)["info"]["battery"] == 12
    finally:
        await client.close()
        app.db.close()


async def test_emergency_goes_to_contacts_and_admins(tmp_path: Path, monkeypatch: Any) -> None:
    client, app, _, bearer = await _paired(tmp_path)
    sent: list[tuple[str, str, str]] = []

    async def send(provider: str, chat: str, reply: Reply) -> None:
        sent.append((provider, chat, reply.text))

    async def admins(text: str) -> None:
        sent.append(("admins", "", text))

    app.providers["signal"] = SimpleNamespace(supports_buttons=False)
    app.escalation.contacts = [("signal", "+49170"), ("discord", "1")]  # discord is not running
    monkeypatch.setattr(app.messenger, "send", send)
    monkeypatch.setattr(app.messenger, "notify_admins", admins)
    try:
        body = {"latitude": 52.5, "longitude": 13.4, "accuracy": 8.0, "battery": 40}
        response = await client.post("/api/mobile/emergency", json=body, headers=bearer)
        assert (await response.json())["contacts"] == 1
        assert [s[0] for s in sent] == ["signal", "admins"]
        assert "Handy Lena" in sent[0][2] and "openstreetmap.org" in sent[0][2]
        assert "40" in sent[0][2]
    finally:
        await client.close()
        app.db.close()


def _apk() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("AndroidManifest.xml", "x")
        archive.writestr("classes.dex", "y")
    return buffer.getvalue()


async def test_app_upload_and_update_check(tmp_path: Path) -> None:
    client, app, headers, bearer = await _paired(tmp_path)

    def form(password: str, body: bytes) -> Any:
        from aiohttp import FormData

        data = FormData()
        data.add_field("password", password)
        data.add_field("version_code", "7")
        data.add_field("version_name", "1.2")
        data.add_field("file", body, filename="app.apk")
        return data

    try:
        url = "/api/mobile/app"
        assert (await client.post(url, data=form("falsch", _apk()), headers=headers)).status == 403
        assert (
            await client.post(url, data=form(PASSWORD, b"no zip"), headers=headers)
        ).status == 400
        assert (await client.post(url, data=form(PASSWORD, _apk()), headers=headers)).status == 200
        latest = await (await client.get("/api/mobile/app/latest", headers=bearer)).json()
        assert latest["version_code"] == 7 and len(latest["sha256"]) == 64
        apk = await client.get("/api/mobile/app/apk", headers=bearer)
        assert await apk.read() == _apk()
        client.session.cookie_jar.clear()
        assert (await client.get("/api/mobile/app/apk")).status == 401
    finally:
        await client.close()
        app.db.close()


def test_calendar_entries_force_away_and_keep_no_titles(db: Any) -> None:
    modes = ModeController(db, None, Mode.HOME, True)
    modes.update_presence(Presence.HOME)  # a phone in the Wi-Fi does not matter here
    plan = ScheduleRunner(db, modes, ZoneInfo("UTC"))
    events = [{"start": NOW.isoformat(), "end": (NOW + timedelta(hours=1)).isoformat(),
               "title": "Arzt"}]  # fmt: skip
    cleaned = clean_calendar(events)
    assert "title" not in cleaned[0]
    db.set(CALENDAR_KEY, cleaned)
    db.set(PLAN_KEY, {"enabled": False, "blocks": [], "respect_presence": True})
    plan.tick(NOW + timedelta(minutes=5))
    assert modes.effective is Mode.AWAY
    plan.tick(NOW + timedelta(hours=2))
    assert modes.effective is Mode.HOME
    with pytest.raises(ValueError, match="calendar"):
        clean_calendar([{"start": NOW.isoformat(), "end": NOW.isoformat()}])


async def test_viewers_can_pair_but_not_find(tmp_path: Path) -> None:
    client, app = await _client(tmp_path, mobile_enabled=True)
    try:
        headers = {"X-CSRF-Token": await _login(client)}
        body = {"username": "oma", "password": "lange-passphrase-1", "role": "viewer"}
        await client.post("/api/dashboard-users", json=body, headers=headers)
        client.session.cookie_jar.clear()
        await client.post("/api/login", json={"username": "oma", "password": body["password"]})
        session = await (await client.get("/api/session")).json()
        viewer = {"X-CSRF-Token": session["csrf"]}
        pair = {"name": "Oma", "platform": "android"}
        assert (await client.post("/api/mobile/pair", json=pair, headers=viewer)).status == 200
        assert (await client.get("/api/mobile/devices")).status == 403
        assert (await client.post("/api/mobile/devices/1/find", headers=viewer)).status == 403
    finally:
        await client.close()
        app.db.close()


async def test_a_call_needs_the_same_origin(tmp_path: Path) -> None:
    client, app, _headers, _ = await _paired(tmp_path)
    try:
        app.audio = SimpleNamespace(playback_enabled=True, settings=SimpleNamespace(speaker="hw:1"))
        response = await client.get(
            "/api/live/cam/call", headers={"Origin": "https://evil.example"}
        )
        assert response.status == 403
    finally:
        await client.close()
        app.db.close()


async def test_removing_a_phone_takes_everything(tmp_path: Path) -> None:
    client, app, _headers, bearer = await _paired(tmp_path)
    try:
        # The app's stored login and the login it paired with both end.
        client.session.cookie_jar.clear()
        assert (await client.post("/api/mobile/session", headers=bearer)).status == 200
        assert (await client.get("/api/overview")).status == 200
        phone_cookies = client.session.cookie_jar.filter_cookies(client.make_url("/"))

        client.session.cookie_jar.clear()
        admin = {"X-CSRF-Token": await _login(client)}
        removed: list[int] = []
        app.dashboard.wg_peers = SimpleNamespace(remove=removed.append)
        app.settings.wireguard_enabled = True
        app.mobile.set_vpn_peer(1, 7)
        assert (await client.delete("/api/mobile/devices/1", headers=admin)).status == 200
        assert removed == [7]

        client.session.cookie_jar.clear()
        client.session.cookie_jar.update_cookies(phone_cookies)
        assert (await client.get("/api/overview")).status == 401
        assert (await client.post("/api/mobile/session", headers=bearer)).status == 401
    finally:
        await client.close()
        app.db.close()


async def test_the_login_that_paired_ends_with_the_phone(tmp_path: Path) -> None:
    client, app, headers, _ = await _paired(tmp_path)
    try:
        assert (await client.get("/api/overview")).status == 200
        assert (await client.delete("/api/mobile/devices/1", headers=headers)).status == 200
        assert (await client.get("/api/overview")).status == 401
    finally:
        await client.close()
        app.db.close()


async def _with_vpn(tmp_path: Path, app: Any) -> None:
    from pi_guard.network.wgpeers import PeerStore, generate_keypair

    app.settings.wireguard_enabled = True
    app.settings.wireguard_endpoint = "home.example:51820"
    app.settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    (app.settings.runtime_dir / "wireguard.status").write_text(
        f"written\t2026-11-02T10:00:00Z\n(hidden)\t{generate_keypair()[1]}\t51820\toff\n"
    )
    app.dashboard.wg_peers = PeerStore(app.db, tmp_path, b"k" * 32)


async def test_the_app_gets_its_own_vpn_access_and_keeps_it(tmp_path: Path) -> None:
    client, app, _, bearer = await _paired(tmp_path)
    try:
        await _with_vpn(tmp_path, app)
        first = await client.post("/api/mobile/vpn", json={"full_tunnel": True}, headers=bearer)
        assert first.status == 200 and first.headers["Cache-Control"] == "no-store"
        body = await first.json()
        assert body["name"] == "App 1 Handy Lena"
        assert "Endpoint = home.example:51820" in body["config"]
        again = await client.post("/api/mobile/vpn", json={}, headers=bearer)
        assert (await again.json())["config"] == body["config"]
        assert len(app.dashboard.wg_peers.list()) == 1
        assert app.mobile.get(1)["vpn_peer"] == app.dashboard.wg_peers.list()[0]["id"]
    finally:
        await client.close()
        app.db.close()


async def test_an_access_removed_in_the_dashboard_is_replaced(tmp_path: Path) -> None:
    client, app, _, bearer = await _paired(tmp_path)
    try:
        await _with_vpn(tmp_path, app)
        first = await (await client.post("/api/mobile/vpn", json={}, headers=bearer)).json()
        app.dashboard.wg_peers.remove(app.mobile.get(1)["vpn_peer"])
        second = await (await client.post("/api/mobile/vpn", json={}, headers=bearer)).json()
        assert second["config"] != first["config"]
    finally:
        await client.close()
        app.db.close()


async def test_two_phones_with_a_long_equal_name_both_get_an_access(tmp_path: Path) -> None:
    client, app, headers, _ = await _paired(tmp_path)
    try:
        await _with_vpn(tmp_path, app)
        names = set()
        for _ in range(2):
            body = {"name": "samsung SM-A226B Galaxy A22 5G Dual", "platform": "android"}
            paired = await (
                await client.post("/api/mobile/pair", json=body, headers=headers)
            ).json()
            bearer = {"Authorization": f"Bearer {paired['token']}"}
            response = await client.post("/api/mobile/vpn", json={}, headers=bearer)
            assert response.status == 200
            names.add((await response.json())["name"])
        assert len(names) == 2
    finally:
        await client.close()
        app.db.close()


async def test_a_vpn_access_needs_a_paired_phone_and_wireguard(tmp_path: Path) -> None:
    client, app, _, bearer = await _paired(tmp_path)
    try:
        wrong = {"Authorization": "Bearer null"}
        assert (await client.post("/api/mobile/vpn", json={}, headers=wrong)).status == 401
        assert (await client.post("/api/mobile/vpn", json={}, headers=bearer)).status == 409
    finally:
        await client.close()
        app.db.close()


async def test_the_app_learns_every_way_home(tmp_path: Path) -> None:
    client, app, _, bearer = await _paired(tmp_path)
    try:
        await _with_vpn(tmp_path, app)
        app.settings.wireguard_endpoint_v6 = "pi.example:51820"
        app.settings.wireguard_relay = "vps.example:51820"
        body = await (await client.post("/api/mobile/vpn", json={}, headers=bearer)).json()
        assert [w["kind"] for w in body["ways"]] == ["ipv4", "ipv6", "relay"]
        assert body["ways"][2]["mtu"] == 1340 and "mtu" not in body["ways"][0]
        assert (await client.get("/api/mobile/reach")).status == 401
    finally:
        await client.close()
        app.db.close()


async def test_a_phone_only_reachable_through_the_relay_gets_a_working_access(
    tmp_path: Path,
) -> None:
    client, app, _, bearer = await _paired(tmp_path)
    try:
        await _with_vpn(tmp_path, app)
        app.settings.wireguard_endpoint = None
        app.settings.wireguard_relay = "vps.example:51820"
        body = await (await client.post("/api/mobile/vpn", json={}, headers=bearer)).json()
        assert "Endpoint = vps.example:51820" in body["config"]
        assert "MTU = 1340" in body["config"]
    finally:
        await client.close()
        app.db.close()
