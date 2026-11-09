"""The phone side: pairing, tokens, alerts, finding, emergencies, app updates, calendar."""

from __future__ import annotations

import base64
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from aiohttp import CookieJar
from aiohttp.test_utils import TestClient

from pi_guard.addons.mobile import MobileHub, MobileProvider, clean_report, map_link
from pi_guard.cameras.pipeline import SnapshotUnavailableError
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
        hub.create("x", "commodore", "admin")
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

    async def admins(text: str, photos: Any = (), tone: str = "") -> None:
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


async def _sos(tmp_path: Path, monkeypatch: Any, take: Any, send: Any) -> tuple[Any, Any]:
    client, app, _, bearer = await _paired(tmp_path)
    app.providers["signal"] = object()
    app.cameras.clear()
    app.cameras.update({"flur": object(), "hof": object()})

    async def nobody(*_: Any, **__: Any) -> None:
        return None

    monkeypatch.setattr(app.messenger, "notify_admins", nobody)
    monkeypatch.setattr(app.escalation, "contacts", [("signal", "+49")], raising=False)
    monkeypatch.setattr(app, "take_photo", take)
    monkeypatch.setattr(app.messenger, "send", send)
    response = await client.post("/api/mobile/emergency", json={}, headers=bearer)
    return client, (app, await response.json())


async def test_an_emergency_comes_with_what_the_cameras_see(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[Reply] = []

    async def take(camera: str, prefix: str = "") -> Path:
        return tmp_path / f"{prefix}_{camera}.jpg"

    async def send(_: str, __: str, reply: Reply) -> None:
        sent.append(reply)

    client, (app, body) = await _sos(tmp_path, monkeypatch, take, send)
    try:
        assert body["contacts"] == 1 and "NOT" in sent[0].text.upper()
        assert [r.photo.name for r in sent if r.photo] == [
            "emergency_flur.jpg",
            "emergency_hof.jpg",
        ]
        assert sent[0].tone == "alert"
    finally:
        await client.close()
        app.db.close()


async def test_a_camera_that_fails_never_stops_an_emergency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[Reply] = []

    async def take(camera: str, prefix: str = "") -> Path:
        raise SnapshotUnavailableError("no picture")

    async def send(_: str, __: str, reply: Reply) -> None:
        sent.append(reply)

    client, (app, body) = await _sos(tmp_path, monkeypatch, take, send)
    try:
        assert body["contacts"] == 1 and sent[0].photo is None
    finally:
        await client.close()
        app.db.close()


async def test_the_words_go_out_even_if_the_picture_cannot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[Reply] = []

    async def take(camera: str, prefix: str = "") -> Path:
        return tmp_path / "big.jpg"

    async def send(_: str, __: str, reply: Reply) -> None:
        if reply.photo:
            raise ProviderUnavailableError("too large")
        sent.append(reply)

    client, (app, body) = await _sos(tmp_path, monkeypatch, take, send)
    try:
        assert body["contacts"] == 1 and len(sent) == 1
    finally:
        await client.close()
        app.db.close()


async def test_map_tiles_come_through_the_pi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pi_guard.web import api_mobile

    asked: list[Any] = []

    class Fake:
        def __init__(self, **kwargs: Any) -> None:
            self.headers = kwargs["headers"]

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def get(self, url: str) -> Any:
            asked.append((url, self.headers["User-Agent"]))
            return SimpleNamespace(content=b"png", raise_for_status=lambda: None)

    monkeypatch.setattr(api_mobile.httpx, "AsyncClient", Fake)
    api_mobile._tiles.clear()
    client, app, _, _ = await _paired(tmp_path)
    try:
        answer = await client.get("/api/mobile/map/16/34000/22000.png")
        assert answer.status == 200 and await answer.read() == b"png"
        await client.get("/api/mobile/map/16/34000/22000.png")
        assert len(asked) == 1  # kept, OpenStreetMap asks for that
        assert asked[0][0].endswith("/16/34000/22000.png") and "pi-guard" in asked[0][1]
        assert (await client.get("/api/mobile/map/25/1/1.png")).status == 404
        assert (await client.get("/api/mobile/map/2/9/1.png")).status == 404
        assert "openstreetmap" not in answer.headers.get("Content-Security-Policy", "")
        client.session.cookie_jar.clear()
        assert (await client.get("/api/mobile/map/16/34000/22000.png")).status == 401
    finally:
        await client.close()
        app.db.close()


async def test_a_computer_gets_its_quantum_keys_swapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pi_guard.web import api_mobile

    client, app, headers, _ = await _paired(tmp_path)
    try:
        await _with_vpn(tmp_path, app)
        answer = await client.post(
            "/api/mobile/pair", json={"name": "Arch PC", "platform": "linux"}, headers=headers
        )
        bearer = {"Authorization": f"Bearer {(await answer.json())['token']}"}
        await client.post("/api/mobile/vpn", json={}, headers=bearer)
        monkeypatch.setattr(api_mobile, "quantum_public_key", lambda: b"s" * 524160)
        key = base64.b64encode(b"c" * 524160).decode()
        response = await client.post("/api/mobile/vpn/pq", json={"key": key}, headers=bearer)
        assert response.status == 200
        assert base64.b64decode((await response.json())["server_key"]) == b"s" * 524160
        peer = app.dashboard.wg_peers.list()[0]
        path = app.dashboard.wg_peers.pq_path(peer["public_key"])
        assert path.read_bytes() == b"c" * 524160
        app.dashboard.wg_peers.remove(peer["id"])
        assert not path.exists()  # gone with the device
    finally:
        await client.close()
        app.db.close()


async def test_only_a_key_of_the_right_size_is_taken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pi_guard.web import api_mobile

    client, app, _, bearer = await _paired(tmp_path)
    try:
        await _with_vpn(tmp_path, app)
        await client.post("/api/mobile/vpn", json={}, headers=bearer)
        monkeypatch.setattr(api_mobile, "quantum_public_key", lambda: b"s" * 524160)
        short = base64.b64encode(b"c" * 10).decode()
        for body in ({"key": short}, {"key": "not base64!"}, {}):
            assert (
                await client.post("/api/mobile/vpn/pq", json=body, headers=bearer)
            ).status == 400
        assert (await client.post("/api/mobile/vpn/pq", json={"key": short})).status == 401
    finally:
        await client.close()
        app.db.close()


async def test_quantum_keys_are_on_by_default_and_can_be_switched_off(tmp_path: Path) -> None:
    from pi_guard.web.api_network import apply_quantum, quantum_flag

    client, app, headers, _ = await _paired(tmp_path)
    try:
        apply_quantum(app.dashboard)
        flag = quantum_flag(app.settings)
        assert flag.exists()
        assert (await (await client.get("/api/network/quantum")).json())["enabled"] is True
        off = await client.put("/api/network/quantum", json={"enabled": False}, headers=headers)
        assert off.status == 200 and not flag.exists()
    finally:
        await client.close()
        app.db.close()


async def _viewer_phone(client: Any, app: Any) -> tuple[Any, dict[str, str]]:
    """A second browser logs in as a viewer and pairs a phone."""
    app.dashboard.auth.add_user("guest", "guest password 1", "viewer")
    other = TestClient(client.server, cookie_jar=CookieJar(unsafe=True))
    await other.start_server()
    await other.post("/api/login", json={"username": "guest", "password": "guest password 1"})
    csrf = (await (await other.get("/api/session")).json())["csrf"]
    paired = await other.post("/api/mobile/pair", json={"name": "Guest", "platform": "android"},
                              headers={"X-CSRF-Token": csrf})  # fmt: skip
    return other, {"Authorization": f"Bearer {(await paired.json())['token']}"}


async def test_removing_a_person_takes_their_phones(tmp_path: Path) -> None:
    client, app, csrf, _ = await _paired(tmp_path)
    other, bearer = await _viewer_phone(client, app)
    try:
        assert (await other.post("/api/mobile/session", headers=bearer)).status == 200
        user_id = app.dashboard.auth.users()[0]["id"]
        assert (await client.delete(f"/api/dashboard-users/{user_id}", headers=csrf)).status == 200
        assert (await other.get("/api/overview")).status == 401
        assert (await other.post("/api/mobile/session", headers=bearer)).status == 401
        assert app.mobile.of_user("guest") == []
    finally:
        await other.close()
        await client.close()
        app.db.close()


async def test_a_phone_of_someone_removed_earlier_is_refused(tmp_path: Path) -> None:
    client, app, _, _ = await _paired(tmp_path)
    other, bearer = await _viewer_phone(client, app)
    try:
        # What older versions left behind: the device stayed when its person went.
        app.dashboard.auth.delete_user(app.dashboard.auth.users()[0]["id"])
        assert app.mobile.of_user("guest")
        assert (await other.post("/api/mobile/session", headers=bearer)).status == 401
    finally:
        await other.close()
        await client.close()
        app.db.close()


async def test_a_new_owner_name_keeps_the_owners_phones(tmp_path: Path) -> None:
    client, app, csrf, bearer = await _paired(tmp_path)
    try:
        response = await client.post(
            "/api/account/credentials", headers=csrf,
            json={"current": PASSWORD, "username": "chief", "password": "a new long password"},
        )  # fmt: skip
        assert response.status == 200
        assert (await client.post("/api/mobile/session", headers=bearer)).status == 200
        assert (await (await client.get("/api/session")).json())["role"] == "admin"
    finally:
        await client.close()
        app.db.close()


async def test_a_deleted_key_file_takes_its_logins(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        auth = app.dashboard.auth
        secret = auth.create_keyfile("tablet")
        token = auth.create_session("key:tablet", "127.0.0.1", "test")
        assert auth.exists("key:tablet")
        assert auth.check_keyfile(secret) == "tablet"
        assert auth.delete_keyfile("tablet")
        assert auth.session(token) is None
        assert not auth.exists("key:tablet")
    finally:
        await client.close()
        app.db.close()


async def test_viewers_get_no_vpn_access(tmp_path: Path) -> None:
    client, app, _, _ = await _paired(tmp_path)
    other, bearer = await _viewer_phone(client, app)
    try:
        response = await other.post("/api/mobile/vpn", json={}, headers=bearer)
        assert response.status == 403 and await response.text() == "web.vpn_admins_only"
        response = await other.post("/api/mobile/vpn/pq", json={"key": ""}, headers=bearer)
        assert response.status == 403
    finally:
        await other.close()
        await client.close()
        app.db.close()


async def test_someone_changed_to_viewer_loses_the_vpn_access(tmp_path: Path) -> None:
    client, app, csrf, _ = await _paired(tmp_path)
    other, _ = await _viewer_phone(client, app)
    try:
        removed: list[int] = []
        app.dashboard.wg_peers = SimpleNamespace(remove=removed.append)
        app.settings.wireguard_enabled = True
        user_id = app.dashboard.auth.users()[0]["id"]
        device = app.mobile.of_user("guest")[0]["id"]
        url = f"/api/dashboard-users/{user_id}"
        assert (await client.patch(url, json={"role": "admin"}, headers=csrf)).status == 200
        assert app.users.get("mobile", str(device)).role.value == "admin"
        app.mobile.set_vpn_peer(device, 7)
        assert (await client.patch(url, json={"role": "viewer"}, headers=csrf)).status == 200
        assert removed == [7]
        assert not app.mobile.get(device)["vpn_peer"]
        assert app.users.get("mobile", str(device)).role.value == "viewer"
    finally:
        await other.close()
        await client.close()
        app.db.close()


async def test_an_alert_button_on_the_phone(tmp_path: Path) -> None:
    from pi_guard.core.events import Event, EventType

    client, app, _, bearer = await _paired(tmp_path)
    try:
        app.escalation.record(Event(EventType.MOTION_DETECTED, camera="living_room"), None)
        app.escalation.record(Event(EventType.UNKNOWN_PERSON, camera="living_room"), None)
        ok = await client.post("/api/mobile/action", json={"action": "ack"}, headers=bearer)
        assert ok.status == 200 and (await ok.json())["acknowledged"] >= 1
        assert app.escalation.open_alerts() == []
        no_speaker = await client.post(
            "/api/mobile/action", json={"action": "alarm"}, headers=bearer
        )
        assert no_speaker.status == 409
        bad = await client.post("/api/mobile/action", json={"action": "unlock"}, headers=bearer)
        assert bad.status == 400
        nobody = await client.post("/api/mobile/action", json={"action": "ack"})
        assert nobody.status == 401
    finally:
        await client.close()
        app.db.close()


@pytest.mark.parametrize(
    ("provider", "part"),
    [("openstreetmap", "openstreetmap.org/?mlat=52.516300&mlon=13.377700"),
     ("google", "google.com/maps/search/?api=1&query=52.516300,13.377700"),
     ("apple", "maps.apple.com/?ll=52.516300,13.377700"),
     ("geo", "geo:52.516300,13.377700"),
     ("nonsense", "openstreetmap.org")],
)  # fmt: skip
def test_a_place_opens_in_the_chosen_map(provider: str, part: str) -> None:
    from pi_guard.addons.mobile import map_link

    assert part in (map_link({"latitude": 52.5163, "longitude": 13.3777}, provider) or "")


async def test_a_phone_is_asked_for_its_place_without_ringing(tmp_path: Path) -> None:
    client, app, headers, _ = await _paired(tmp_path)
    try:
        device_id = (await (await client.get("/api/mobile/devices")).json())["devices"][0]["id"]
        offline = await client.post(f"/api/mobile/devices/{device_id}/locate", headers=headers)
        assert offline.status == 409  # not connected
        received: list[str] = []

        async def phone(text: str) -> None:
            received.append(text)

        app.mobile.attach(device_id, phone)
        asked = await client.post(f"/api/mobile/devices/{device_id}/locate", headers=headers)
        assert asked.status == 200 and json.loads(received[0]) == {"type": "locate"}  # no ring
    finally:
        await client.close()
        app.db.close()


async def _sos_files(tmp_path: Path, monkeypatch: Any) -> tuple[Any, Any, Any, list[Reply]]:
    client, app, headers, bearer = await _paired(tmp_path)
    sent: list[Reply] = []

    async def send(_: str, __: str, reply: Reply) -> None:
        assert (reply.photo or reply.audio or tmp_path).exists()  # still there while sending
        sent.append(reply)

    async def admins(*_: Any, **__: Any) -> None:
        return None

    app.providers["signal"] = object()
    app.cameras.clear()
    monkeypatch.setattr(app.escalation, "contacts", [("signal", "+49")], raising=False)
    monkeypatch.setattr(app.messenger, "send", send)
    monkeypatch.setattr(app.messenger, "notify_admins", admins)
    return client, app, (headers, bearer), sent


async def test_the_phone_sends_photo_and_sound_only_during_its_emergency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, app, (_, bearer), sent = await _sos_files(tmp_path, monkeypatch)
    photo = b"\xff\xd8\xff\xe0" + b"0" * 100
    sound = b"\x00\x00\x00\x18ftypM4A " + b"0" * 100
    url = "/api/mobile/emergency/media"
    try:
        before = await client.post(f"{url}?kind=photo", data=photo, headers=bearer)
        assert before.status == 409  # no emergency, no upload
        await client.post("/api/mobile/emergency", json={}, headers=bearer)
        sent.clear()
        assert (await client.post(f"{url}?kind=photo", data=photo, headers=bearer)).status == 200
        assert (await client.post(f"{url}?kind=audio", data=sound, headers=bearer)).status == 200
        assert sent[0].photo is not None and sent[1].audio is not None
        assert sent[0].tone == "alert"
        assert not sent[0].photo.exists()  # nothing of it stays on the Pi
        wrong = await client.post(f"{url}?kind=photo", data=sound, headers=bearer)
        assert wrong.status == 400
        for _ in range(4):
            await client.post(f"{url}?kind=photo", data=photo, headers=bearer)
        assert (await client.post(f"{url}?kind=photo", data=photo, headers=bearer)).status == 429
        assert (await client.post(f"{url}?kind=photo", data=photo)).status == 401
    finally:
        await client.close()
        app.db.close()


async def test_places_are_kept_during_an_emergency_and_deleted_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, app, (headers, bearer), sent = await _sos_files(tmp_path, monkeypatch)
    try:
        place = {"latitude": 52.5, "longitude": 13.4, "accuracy": 9.0}
        await client.post("/api/mobile/report", json=place, headers=bearer)
        assert app.mobile.track(1) == []  # no emergency: only the last place
        body = await (await client.post("/api/mobile/emergency", json=place, headers=bearer)).json()
        assert body["track_minutes"] == 30
        await client.post("/api/mobile/report", json={**place, "latitude": 52.6}, headers=bearer)
        track = await (await client.get("/api/mobile/devices/1/track", headers=headers)).json()
        assert [p["latitude"] for p in track["track"]] == [52.6, 52.5]
        assert track["state"]["kind"] == "emergency" and "openstreetmap" in track["track"][0]["map"]
        listed = await (await client.get("/api/mobile/devices", headers=headers)).json()
        assert listed["devices"][0]["tracking"]["kind"] == "emergency"
        sent.clear()
        assert (await client.post("/api/mobile/emergency/end", headers=bearer)).status == 200
        assert "safe" in sent[0].text
        assert app.mobile.track(1) == [] and not app.mobile.tracking(1)
    finally:
        await client.close()
        app.db.close()


async def test_an_emergency_track_ends_on_its_own(db: Any) -> None:
    hub = MobileHub(db)
    device, _ = hub.create("Handy", "android", "admin")
    hub.start(device, "emergency")
    assert hub.tracking(device)
    state = db.get("mobile.tracking")
    state[str(device)]["since"] = (datetime.now(UTC) - timedelta(minutes=31)).isoformat()
    db.set("mobile.tracking", state)
    assert not hub.tracking(device) and hub.states() == {}
    hub.report(device, {"latitude": 1.0, "longitude": 2.0})
    assert hub.track(device) == []


async def test_a_lost_phone_needs_the_password_and_learns_it_when_online(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, app, (headers, bearer), _ = await _sos_files(tmp_path, monkeypatch)
    url = "/api/mobile/devices/1/lost"
    try:
        wrong = await client.post(url, json={"password": "nope"}, headers=headers)
        assert wrong.status == 403 and not app.mobile.tracking(1)
        body = {"password": PASSWORD, "message": "Bitte  anrufen:\n 0170"}
        assert (await client.post(url, json=body, headers=headers)).status == 200
        socket = await client.ws_connect("/api/mobile/ws", headers=bearer)
        message = await socket.receive_json()
        assert message == {"type": "lost", "message": "Bitte anrufen: 0170", "every": 60}
        await client.post("/api/mobile/report", json={"latitude": 1.0, "longitude": 2.0},
                          headers=bearer)  # fmt: skip
        assert len(app.mobile.track(1)) == 1
        # The phone cannot end its own search: whoever holds it may not be its owner.
        assert (await client.post("/api/mobile/emergency/end", headers=bearer)).status == 409
        assert (await client.post("/api/mobile/devices/1/found", headers=headers)).status == 200
        assert (await socket.receive_json()) == {"type": "found"}
        assert app.mobile.track(1) == [] and not app.mobile.tracking(1)
        await socket.close()
        missing = await client.post("/api/mobile/devices/9/lost", json=body, headers=headers)
        assert missing.status == 404
    finally:
        await client.close()
        app.db.close()


async def test_removing_a_phone_deletes_its_track(db: Any) -> None:
    hub = MobileHub(db)
    device, _ = hub.create("Handy", "android", "admin")
    hub.start(device, "lost")
    hub.report(device, {"latitude": 1.0, "longitude": 2.0})
    hub.delete(device)
    assert hub.track(device) == [] and hub.states() == {}


async def test_the_home_microphone_records_a_clip_after_an_sos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from pi_guard.cameras.live import AudioFanout
    from pi_guard.web import api_mobile

    sent: list[tuple[str, bytes]] = []

    async def everyone(_: Any, text: str, photo: Any = None, audio: Any = None) -> int:
        sent.append((text, audio.read_bytes()))
        return 1

    fanout = AudioFanout()
    camera = SimpleNamespace(microphone="hw:1", segment_dir=tmp_path, live_fps=5, live_audio=fanout)
    sounds = SimpleNamespace(path=lambda _: None)
    audio = SimpleNamespace(recording_enabled=False, playback_enabled=False, sounds=sounds)
    settings = SimpleNamespace(language="en")
    db = SimpleNamespace(audit=lambda *_: None)
    app = SimpleNamespace(audio=audio, cameras={"flur": camera}, settings=settings, db=db)
    d = SimpleNamespace(app=app, microphones={})
    monkeypatch.setattr(api_mobile, "HOME_CLIP_SECONDS", 0.2)
    monkeypatch.setattr(api_mobile, "_to_everyone", everyone)
    await api_mobile._home_clip(d, "Handy")
    assert sent == []  # microphone recording is off: nothing is recorded
    audio.recording_enabled = True
    job = asyncio.create_task(api_mobile._home_clip(d, "Handy"))
    await asyncio.sleep(0.05)
    fanout.publish(b"mp3-1")
    fanout.publish(b"mp3-2")
    await job
    assert sent == [(sent[0][0], b"mp3-1mp3-2")] and "Handy" in sent[0][0]
    assert fanout.listeners == 0
