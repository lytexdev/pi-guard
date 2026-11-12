"""Signal waiting, Wi-Fi codes, weekly plan, support, navigation, scan and storage."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from pi_guard.addons import plugin_updates
from pi_guard.addons.plugin import check_manifest
from pi_guard.addons.plugin_updates import PluginGitError, PluginUpdater
from pi_guard.audio import check
from pi_guard.audio.devices import AudioDevice
from pi_guard.audio.service import AudioError
from pi_guard.audio.sounds import write_wav
from pi_guard.config.settings import Settings
from pi_guard.core.modes import Mode, ModeController, Presence, Selection
from pi_guard.core.schedule import PLAN_KEY, ScheduleRunner, clean_plan
from pi_guard.diagnostics import scan, storage
from pi_guard.messaging.base import Button, ProviderUnavailableError, Reply
from pi_guard.messaging.discord.provider import (
    DiscordProvider,
    chunks,
    components,
)
from pi_guard.messaging.email import provider as email_provider
from pi_guard.messaging.email.provider import EXPORT_KEY, EmailProvider, Mailbox
from pi_guard.messaging.signal.provider import SignalProvider, parse_envelope
from pi_guard.network.fritzbox import FritzBox, FritzError, Wlan
from pi_guard.web import api_tools
from pi_guard.web.api_tools import _wifi_text
from pi_guard.web.settings_schema import BY_KEY, to_env

from .test_web import PASSWORD, _client, _login

OWN = "+491700000000"
MONDAY_NOON = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def runner(db: Any, presence: bool = False) -> tuple[ScheduleRunner, ModeController]:
    modes = ModeController(db, None, Mode.HOME, presence)
    return ScheduleRunner(db, modes, ZoneInfo("UTC")), modes


def work(db: Any, **extra: Any) -> None:
    block = {"day": 0, "start": 8 * 60, "end": 16 * 60, "label": "Arbeit"}
    db.set(PLAN_KEY, clean_plan({"enabled": True, "blocks": [block], **extra}))


def test_plan_switches_at_the_edges_only(db: Any) -> None:
    plan, modes = runner(db)
    work(db)
    plan.tick(MONDAY_NOON)
    assert modes.effective is Mode.AWAY
    plan.tick(MONDAY_NOON + timedelta(hours=5))
    assert modes.effective is Mode.HOME

    # Set by hand during a block: stays, also when the block ends.
    plan.tick(MONDAY_NOON + timedelta(days=7))
    modes.set_manual(Mode.HOME)
    modes.set_manual(Mode.AWAY)
    plan.tick(MONDAY_NOON + timedelta(days=7, minutes=1))
    assert modes.effective is Mode.AWAY
    plan.tick(MONDAY_NOON + timedelta(days=7, hours=5))
    assert modes.effective is Mode.AWAY


def test_plan_does_not_arm_while_someone_is_home(db: Any) -> None:
    plan, modes = runner(db, presence=True)
    modes.update_presence(Presence.HOME)
    work(db)
    plan.tick(MONDAY_NOON)
    assert modes.effective is Mode.HOME and modes.state.selection is Selection.AUTO


def test_plan_hands_back_to_presence(db: Any) -> None:
    plan, modes = runner(db, presence=True)
    modes.update_presence(Presence.HOME)
    work(db, respect_presence=False)
    plan.tick(MONDAY_NOON)
    assert modes.effective is Mode.AWAY
    plan.tick(MONDAY_NOON + timedelta(hours=5))
    assert modes.state.selection is Selection.AUTO and modes.effective is Mode.HOME


@pytest.mark.parametrize("block", [
    {"day": 7, "start": 0, "end": 60}, {"day": 0, "start": 60, "end": 60},
    {"day": 0, "start": 0, "end": 1441}, {"day": "0", "start": 0, "end": 60},
])  # fmt: skip
def test_invalid_blocks(block: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="block"):
        clean_plan({"blocks": [block]})


def test_note_to_self_is_a_chat_but_other_sent_messages_are_not() -> None:
    def copy(destination: str) -> dict[str, Any]:
        sent = {"destinationNumber": destination, "message": "foto"}
        return {"envelope": {"sourceNumber": OWN, "syncMessage": {"sentMessage": sent}}}

    incoming = parse_envelope(copy(OWN), OWN)
    assert incoming is not None and incoming.sender_id == OWN and incoming.text == "foto"
    assert parse_envelope(copy("+491711111111"), OWN) is None


async def test_signal_waits_instead_of_crashing() -> None:
    provider = SignalProvider(OWN, "127.0.0.1:1")

    async def handler(_: Any) -> None:
        raise AssertionError

    task = asyncio.create_task(provider.run(handler))
    await asyncio.sleep(0.05)
    assert not task.done() and not provider.connected
    task.cancel()


def test_wifi_code_escapes_special_characters() -> None:
    assert _wifi_text("Home;1", 'a:b"c\\d') == 'WIFI:T:WPA;S:Home\\;1;P:a\\:b\\"c\\\\d;;'


class Fritz:
    def __init__(self) -> None:
        self.keys: dict[int, str] = {1: "secret-key-1", 2: "secret-key-1", 3: "guest-key-1"}
        self.blocked: set[str] = set()

    async def wlans(self) -> list[Wlan]:
        return [Wlan(1, "Home", True, 1, "n", 3, False),
                Wlan(2, "Home 5G", True, 44, "ac", 1, False),
                Wlan(3, "Guests", True, 1, "n", 0, True)]  # fmt: skip

    async def wifi_key(self, index: int) -> str:
        return self.keys[index]

    async def set_wifi_key(self, index: int, key: str) -> None:
        self.keys[index] = key

    async def internet_allowed(self, ip: str) -> bool:
        return ip not in self.blocked

    async def set_internet(self, ip: str, allowed: bool) -> None:
        (self.blocked.discard if allowed else self.blocked.add)(ip)


async def test_wifi_access_and_blocking_need_the_password(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        headers = {"X-CSRF-Token": await _login(client)}
        no_box = await client.post("/api/network/wifi/qr", json={"index": 3}, headers=headers)
        assert no_box.status == 409
        typed = {"ssid": "Home", "key": "12345678"}
        response = await client.post("/api/network/wifi/qr", json=typed, headers=headers)
        assert (await response.json())["qr"].startswith("data:image/svg+xml")

        app.fritz = box = Fritz()
        guest = await client.post("/api/network/wifi/qr", json={"index": 3}, headers=headers)
        assert (await guest.json())["ssid"] == "Guests"
        details = [r[0] for r in app.db.execute("SELECT detail FROM audit")]
        assert not any("guest-key-1" in d for d in details)

        url = "/api/network/internet/192.168.178.50"
        wrong = {"allowed": False, "password": "falsch"}
        assert (await client.post(url, json=wrong, headers=headers)).status == 403
        assert not box.blocked
        right = {"allowed": False, "password": PASSWORD}
        assert (await client.post(url, json=right, headers=headers)).status == 200
        assert (await (await client.get(url)).json())["allowed"] is False
        public = "/api/network/internet/8.8.8.8"
        assert (await client.post(public, json=right, headers=headers)).status == 400

        renew = "/api/network/wifi/3/renew"
        assert (await client.post(renew, json={}, headers=headers)).status == 403
        new = await client.post(renew, json={"password": PASSWORD}, headers=headers)
        assert new.status == 200
        assert box.keys[3] == (await new.json())["key"] and box.keys[1] == "secret-key-1"
    finally:
        await client.close()
        app.db.close()


async def test_fritzbox_sends_arguments_escaped(monkeypatch: pytest.MonkeyPatch) -> None:
    box = FritzBox("192.0.2.1", "user", "password")
    sent: list[tuple[str, dict[str, str] | None]] = []

    async def call(service: str, path: str, action: str, args: Any = None) -> str:
        sent.append((action, args))
        return "<NewKeyPassphrase>a&amp;b</NewKeyPassphrase><NewPreSharedKey>ff</NewPreSharedKey>"

    monkeypatch.setattr(box, "_call", call)
    assert await box.wifi_key(1) == "a&b"
    await box.set_wifi_key(1, "new-key-123")
    assert sent[-1][0] == "SetSecurityKeys" and sent[-1][1]["NewKeyPassphrase"] == "new-key-123"  # type: ignore[index]
    assert sent[-1][1]["NewPreSharedKey"] == "ff"  # type: ignore[index]
    assert issubclass(FritzError, Exception)


async def test_support_navigation_and_plan_api(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        headers = {"X-CSRF-Token": await _login(client)}
        body = {"name": "Lytex", "phone": "+49 170", "email": "", "address": "", "x": "no"}
        assert (await client.put("/api/support", json=body, headers=headers)).status == 200
        order = {"order": ["live", "overview"]}
        assert (await client.post("/api/me/nav", json=order, headers=headers)).status == 200
        bad = {"order": ["../x"]}
        assert (await client.post("/api/me/nav", json=bad, headers=headers)).status == 400
        session = await (await client.get("/api/session")).json()
        assert session["support"]["name"] == "Lytex" and "x" not in session["support"]
        assert session["nav"] == ["live", "overview"]
        plan = {"enabled": True, "blocks": [{"day": 1, "start": 60, "end": 120, "label": "x"}]}
        assert (await client.put("/api/schedule", json=plan, headers=headers)).status == 200
        assert (await (await client.get("/api/schedule")).json())["blocks"][0]["day"] == 1
        wrong = {"blocks": [{"day": 9, "start": 0, "end": 1}]}
        assert (await client.put("/api/schedule", json=wrong, headers=headers)).status == 400
        client.session.cookie_jar.clear()
        assert (await (await client.get("/api/session")).json())["support"] == {}
    finally:
        await client.close()
        app.db.close()


def test_own_checks_find_problems(tmp_path: Path) -> None:
    class Settings:
        dashboard_tls = False
        dashboard_enabled = True
        data_dir = tmp_path

    class App:
        settings = Settings
        dashboard = None

    tmp_path.chmod(0o755)
    changed = {"checked": 10, "changed": ["pi_guard/app.py"]}
    results = {c["id"]: c for c in scan.own_checks(App, [22, 8443, 4444], changed)}
    assert results["tls"]["status"] == "warn" and results["data_rights"]["status"] == "bad"
    assert results["integrity"]["status"] == "bad" and "app.py" in results["integrity"]["detail"]
    assert results["ports"]["status"] == "warn" and results["ports"]["detail"] == "4444"


def test_root_report_is_judged() -> None:
    results = {c["id"]: c["status"] for c in scan.root_checks({
        "updates": 3, "security_updates": 1, "firewall": False, "unattended": True,
        "ssh": {"permitrootlogin": "no", "passwordauthentication": "yes"},
        "tmp_programs": ["/tmp/miner"], "rootkit_tool": None,
    })}  # fmt: skip
    assert results["updates"] == "bad" and results["firewall"] == "bad"
    assert results["ssh"] == "warn" and results["suspicious"] == "bad"
    assert results["rootkits"] == "unknown"
    assert scan.root_checks(None)[0]["status"] == "unknown"


async def test_scan_runs_and_reports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, app = await _client(tmp_path)
    monkeypatch.setattr(scan, "HELPER", tmp_path / "missing")
    sent: list[str] = []

    async def notify(text: str) -> None:
        sent.append(text)

    monkeypatch.setattr(app.messenger, "notify_admins", notify)
    try:
        headers = {"X-CSRF-Token": await _login(client)}
        response = await client.post("/api/scan", json={"report": True}, headers=headers)
        assert response.status == 200
        for _ in range(100):
            data = await (await client.get("/api/scan")).json()
            if not data["running"]:
                break
            await asyncio.sleep(0.05)
        ids = [c["id"] for c in data["last"]["checks"]]
        assert "password" in ids and "root_job" in ids
        assert len(sent) == 1
    finally:
        await client.close()
        app.db.close()


async def test_storage_cleans_only_old_files(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        headers = {"X-CSRF-Token": await _login(client)}
        cam = app.settings.recordings_path / "cam"
        cam.mkdir(parents=True)
        old, new = cam / "old.mp4", cam / "new.mp4"
        old.write_bytes(b"x" * 100)
        new.write_bytes(b"y" * 50)
        past = time.time() - 10 * 86400
        os.utime(old, (past, past))
        evidence = app.media_dir / "evidence"
        evidence.mkdir()
        (evidence / "proof.mp4").write_bytes(b"z")

        data = await (await client.get("/api/storage")).json()
        item = next(i for i in data["items"] if i["id"] == "recordings")
        assert item["bytes"] == 150 and item["files"] == 2

        for bad in ({"target": "evidence", "days": 1}, {"target": "recordings", "days": 0},
                    {"target": "recordings", "days": 1, "camera": "../.."}):  # fmt: skip
            assert (
                await client.post("/api/storage/clean", json=bad, headers=headers)
            ).status == 400
        good = {"target": "recordings", "days": 7, "camera": "cam"}
        result = await (await client.post("/api/storage/clean", json=good, headers=headers)).json()
        assert result["files"] == 1 and result["bytes"] == 100
        assert new.exists() and not old.exists() and (evidence / "proof.mp4").exists()
    finally:
        await client.close()
        app.db.close()


def test_analysis_names_cameras(tmp_path: Path) -> None:
    assert storage.CLEANABLE[0] == "recordings" and "evidence" not in storage.CLEANABLE
    assert json.dumps(scan.check("x", "ok", 1)) == '{"id": "x", "status": "ok", "detail": "1"}'


# --- Discord, against a small stand-in for its gateway and REST API --------------------------


class FakeDiscord:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.files: list[bytes] = []
        self.acks: list[int] = []
        self.identify: dict[str, Any] = {}
        self.ws: Any = None
        self.limited = 0
        self.close_code: int | None = None
        app = web.Application()
        app.add_routes([
            web.get("/gateway/bot", self.gateway), web.get("/ws", self.socket),
            web.post("/users/@me/channels", self.channel),
            web.post("/channels/{id}/messages", self.message),
            web.post("/interactions/{id}/{token}/callback", self.callback),
        ])  # fmt: skip
        self.server = TestServer(app)

    async def gateway(self, request: web.Request) -> web.Response:
        if request.headers["Authorization"] != "Bot good-token":
            return web.json_response({}, status=401)
        return web.json_response({"url": str(self.server.make_url("/ws"))})

    async def socket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.ws = ws
        await ws.send_json({"op": 10, "d": {"heartbeat_interval": 60000}})
        async for frame in ws:
            data = json.loads(frame.data)
            if data["op"] == 2:
                self.identify = data["d"]
                if self.close_code:
                    await ws.close(code=self.close_code)
                    break
                await self.dispatch("READY", {"user": {"id": "99", "username": "guard"}})
        return ws

    async def dispatch(self, name: str, data: dict[str, Any]) -> None:
        await self.ws.send_json({"op": 0, "s": 1, "t": name, "d": data})

    async def channel(self, request: web.Request) -> web.Response:
        body = await request.json()
        return web.json_response({"id": f"dm{body['recipient_id']}"})

    async def message(self, request: web.Request) -> web.Response:
        if self.limited:
            self.limited -= 1
            return web.json_response({"retry_after": 0.01}, status=429)
        if request.content_type == "multipart/form-data":
            fields = await request.post()
            payload = fields["payload_json"]
            text = payload.file.read() if hasattr(payload, "file") else payload
            self.sent.append({"channel": request.match_info["id"], **json.loads(text)})
            self.files.append(fields["files[0]"].file.read())  # type: ignore[union-attr]
        else:
            self.sent.append({"channel": request.match_info["id"], **await request.json()})
        return web.json_response({"id": "1"})

    async def callback(self, request: web.Request) -> web.Response:
        self.acks.append((await request.json())["type"])
        return web.Response(status=204)


@pytest.fixture
async def discord(tmp_path: Path) -> Any:
    fake = FakeDiscord()
    await fake.server.start_server()
    provider = DiscordProvider("good-token", tmp_path / "up", 1000, lambda user: user == "1",
                               api=str(fake.server.make_url("")).rstrip("/"))  # fmt: skip
    yield fake, provider
    await fake.server.close()


async def _connected(provider: DiscordProvider, got: list[Any]) -> asyncio.Task[None]:
    async def handler(message: Any) -> None:
        got.append(message)

    task = asyncio.create_task(provider.run(handler))
    for _ in range(100):
        if provider.connected:
            return task
        await asyncio.sleep(0.01)
    raise AssertionError("not connected")


async def _wait(condition: Any) -> None:
    for _ in range(200):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out")


async def test_discord_direct_messages_and_buttons(discord: Any) -> None:
    fake, provider = discord
    got: list[Any] = []
    task = await _connected(provider, got)
    try:
        assert fake.identify["intents"] == 1 << 12 and provider.username == "guard"
        author = {"id": "1", "username": "lena", "global_name": "Lena"}
        await fake.dispatch(
            "MESSAGE_CREATE", {"author": author, "channel_id": "c1", "content": "foto"}
        )
        await _wait(lambda: got)
        assert (got[0].provider, got[0].sender_id, got[0].text) == ("discord", "1", "foto")
        assert got[0].sender_name == "Lena" and not got[0].is_group

        # Servers and other bots are ignored.
        await fake.dispatch("MESSAGE_CREATE", {"author": author, "channel_id": "s", "guild_id": "7",
                                               "content": "foto"})  # fmt: skip
        robot = {"id": "2", "bot": True}
        await fake.dispatch("MESSAGE_CREATE", {"author": robot, "channel_id": "b", "content": "x"})
        await fake.dispatch("INTERACTION_CREATE", {
            "type": 3, "id": "5", "token": "t", "user": author, "locale": "de",
            "data": {"custom_id": "status"},
        })  # fmt: skip
        await _wait(lambda: len(got) > 1)
        assert len(got) == 2 and got[1].from_button and got[1].text == "status"
        assert got[1].language_hint == "de" and fake.acks == [6]

        row = (Button("Menü", "menu"),)
        await provider.send("1", Reply("x" * 4500, buttons=(row,)))
        assert [m["channel"] for m in fake.sent] == ["c1", "c1"]  # the channel it wrote from
        assert len(fake.sent[0]["embeds"][0]["description"]) == 4000
        assert "components" not in fake.sent[0]
        assert fake.sent[1]["components"][0]["components"][0]["custom_id"] == "menu"

        await provider.send("42", Reply("alarm"))  # alerts open the direct message themselves
        assert fake.sent[-1]["channel"] == "dm42"
    finally:
        task.cancel()


async def test_discord_sends_files_and_waits_on_rate_limits(discord: Any, tmp_path: Path) -> None:
    fake, provider = discord
    task = await _connected(provider, [])
    try:
        photo = tmp_path / "bild.jpg"
        photo.write_bytes(b"jpeg")
        fake.limited = 1
        await provider.send("1", Reply("Foto", photo=photo))
        card = fake.sent[-1]["embeds"][0]
        assert fake.files == [b"jpeg"] and card["description"] == "Foto"
        assert card["image"]["url"] == "attachment://bild.jpg"
    finally:
        task.cancel()


async def test_discord_only_admins_may_upload(discord: Any, tmp_path: Path) -> None:
    fake, provider = discord
    got: list[Any] = []
    task = await _connected(provider, got)
    try:
        item = {"url": "https://evil.example/x.jpg", "content_type": "image/jpeg", "size": 3,
                "filename": "x.jpg"}  # fmt: skip
        guest = {"id": "2", "username": "g"}
        await fake.dispatch("MESSAGE_CREATE", {"author": guest, "channel_id": "c", "content": "hi",
                                               "attachments": [item]})  # fmt: skip
        await _wait(lambda: got)
        assert got[0].attachment is None
        with pytest.raises(ProviderUnavailableError):  # other hosts are never fetched
            await provider._download(item)
    finally:
        task.cancel()


async def test_discord_bad_token_is_reported(tmp_path: Path) -> None:
    fake = FakeDiscord()
    await fake.server.start_server()
    provider = DiscordProvider("wrong", tmp_path, 1000, lambda _: False,
                               api=str(fake.server.make_url("")).rstrip("/"))  # fmt: skip

    async def handler(_: Any) -> None:
        return None

    task = asyncio.create_task(provider.run(handler))
    await _wait(lambda: provider.problem == "token refused")
    assert not task.done() and not provider.connected  # waits, no crash loop
    task.cancel()
    await fake.server.close()
    assert DiscordProvider("Bot abc ", tmp_path, 1, lambda _: False)._token == "abc"


async def test_discord_not_connected_can_be_retried(tmp_path: Path) -> None:
    provider = DiscordProvider("t", tmp_path, 1000, lambda _: False)
    with pytest.raises(ProviderUnavailableError):
        await provider.send("1", Reply("x"))


def test_discord_menus_fit_its_limits() -> None:
    many = tuple((Button(f"b{i}", f"c{i}"),) for i in range(8))  # one per row, as menus do
    rows = components(many)
    assert len(rows) == 2 and len(rows[0]["components"]) == 5
    assert components(((Button("x", "y" * 101),),)) == []
    assert chunks("a\n" * 2500)[0].endswith("a") and all(len(c) <= 4000 for c in chunks("b" * 9000))


def test_discord_ids_must_be_numbers(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="numbers"):
        Settings(_env_file=None, data_dir=tmp_path, discord_admin_user_ids="lena#1234")  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="TOKEN"):
        Settings(_env_file=None, data_dir=tmp_path, discord_enabled=True,  # type: ignore[call-arg]
                 discord_admin_user_ids="1", telegram_enabled=False)  # fmt: skip


# --- trying out audio devices ----------------------------------------------------------------


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
async def test_test_sound_is_loud_and_silence_is_detected(tmp_path: Path) -> None:
    sound = tmp_path / "test.wav"
    check.write_test_sound(sound)
    assert (await check.peak_db(sound) or -99) > -20
    quiet = tmp_path / "quiet.wav"
    write_wav(quiet, np.zeros(8000, np.float32))
    assert (await check.peak_db(quiet) or float("-inf")) < check.SILENT_BELOW_DB


async def test_only_real_devices_can_be_tested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, app = await _client(tmp_path)
    speaker = AudioDevice(1, "USB", 0, "Box", True, False)
    played: list[str] = []
    recorded: list[str] = []

    async def play(device: str, path: Path) -> None:
        assert path.exists()
        played.append(device)

    async def record(device: str, seconds: int, output: Path) -> None:
        recorded.append(device)
        check.write_test_sound(output)

    monkeypatch.setattr(api_tools.audio_devices, "discover", lambda: [speaker])
    monkeypatch.setattr(check, "play_on", play)
    monkeypatch.setattr(check, "record_from", record)
    try:
        headers = {"X-CSRF-Token": await _login(client)}
        url = "/api/devices/test/speaker"
        evil = {"device": "-f null /etc/passwd"}
        assert (await client.post(url, json=evil, headers=headers)).status == 400
        good = {"device": speaker.alsa_name}
        assert (await client.post(url, json=good, headers=headers)).status == 200
        assert played == [speaker.alsa_name]
        # It has no microphone.
        mic = await client.post("/api/devices/test/microphone", json=good, headers=headers)
        assert mic.status == 400 and not recorded

        monkeypatch.setattr(api_tools.audio_devices, "discover", lambda: [
            AudioDevice(1, "USB", 0, "Box", False, True)])  # fmt: skip
        mic = await client.post("/api/devices/test/microphone", json=good, headers=headers)
        assert mic.status == 200 and mic.headers["X-Silent"] == "0" and await mic.read()
        assert not list(tmp_path.glob("tmp*"))  # the sample is not kept
        actions = [r[0] for r in app.db.execute("SELECT action FROM audit")]
        assert "audio.test_microphone" in actions
    finally:
        await client.close()
        app.db.close()


async def test_a_busy_device_says_so(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def run(args: list[str], limit: float) -> tuple[int, bytes, bytes]:
        return 1, b"", b"cannot open audio device: Device or resource busy"

    monkeypatch.setattr(check, "_run", run)
    with pytest.raises(AudioError) as caught:
        await check.record_from("hw:1", 1, tmp_path / "x.mp3")
    assert caught.value.key == "web.dev_test_busy"


def test_login_methods_are_chosen_from_the_list() -> None:
    field = BY_KEY["dashboard_auth"]
    assert to_env(field, ["password", "webauthn"]) == "password,webauthn"
    with pytest.raises(ValueError, match="choose from"):
        to_env(field, ["password", "magic"])
    with pytest.raises(ValueError, match="one of"):
        to_env(BY_KEY["timezone"], "Mars/Olympus")
    assert to_env(BY_KEY["timezone"], "Europe/Berlin") == "Europe/Berlin"


def test_fritzbox_user_from_the_settings_is_used(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path, fritzbox_username="lytex",  # type: ignore[call-arg]
                        fritzbox_password="x", telegram_enabled=False)  # fmt: skip
    assert to_env(BY_KEY["fritzbox_username"], " lytex ") == "lytex"
    assert settings.fritzbox_username == "lytex"


def test_a_discord_client_secret_is_not_taken_for_the_token() -> None:
    field = BY_KEY["discord_bot_token"]
    with pytest.raises(ValueError, match="bad_token"):
        to_env(field, "a" * 32)
    assert to_env(field, "aaa.bbb.ccc") == "aaa.bbb.ccc"


# --- modules, email, plugin updates, donations -----------------------------------------------


async def test_modules_can_be_switched_off(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        headers = {"X-CSRF-Token": await _login(client)}
        state = {m["id"]: m["enabled"] for m in await (await client.get("/api/modules")).json()}
        assert state == {"telegram": app.settings.telegram_enabled, "signal": False,
                         "discord": False, "whatsapp": False, "email": False,
                         "chatapps": False}  # fmt: skip
        assert "mobile" not in state  # part of pi-guard, under Settings > Apps
        on = await client.put("/api/modules/whatsapp", json={"enabled": True}, headers=headers)
        assert on.status == 200
        session = await (await client.get("/api/session")).json()
        assert session["modules"]["whatsapp"] is True
        assert (await client.put("/api/modules/x", json={}, headers=headers)).status == 404

        app.settings.discord_enabled = True  # in use: off writes the setting too
        await client.put("/api/modules/discord", json={"enabled": False}, headers=headers)
        assert (
            "PI_GUARD_DISCORD_ENABLED=false"
            in (app.settings.data_dir / "dashboard.env").read_text()
        )
    finally:
        await client.close()
        app.db.close()


def _provider(db: Any, sent: list[Any], interval: str = "off") -> EmailProvider:
    box = Mailbox("mail.example", 587, "starttls", "u", "p", "guard@example.org")
    return EmailProvider(box, ["a@example.org", "b@example.org"], db, interval, lambda: b'{"x": 1}')


async def test_email_alert_with_photo_and_export(
    db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[Any] = []
    monkeypatch.setattr(email_provider, "deliver", lambda box, message: sent.append(message))
    provider = _provider(db, sent)
    photo = tmp_path / "alarm.jpg"
    photo.write_bytes(b"jpeg")
    await provider.send("a@example.org", Reply("Bewegung im Flur\nDetails", photo=photo))
    assert sent[0]["Subject"] == "Bewegung im Flur" and sent[0]["To"] == "a@example.org"
    assert [p.get_filename() for p in sent[0].iter_attachments()] == ["alarm.jpg"]
    assert await provider.send_export() == 2
    assert next(sent[-1].iter_attachments()).get_filename().endswith(".json")


def test_email_export_comes_on_schedule(db: Any) -> None:
    provider = _provider(db, [], "weekly")
    assert provider._export_due() is False  # the first one after a full period
    assert provider._export_due() is False
    db.set(EXPORT_KEY, (datetime.now(UTC) - timedelta(days=8)).isoformat())
    assert provider._export_due() is True
    assert _provider(db, [], "off")._export_due() is False


def test_mail_never_goes_out_unencrypted(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            calls.append("connect")

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def starttls(self, context: Any) -> None:
            calls.append("starttls")

        def login(self, user: str, password: str) -> None:
            calls.append("login")

        def send_message(self, message: Any) -> None:
            calls.append("send")

    monkeypatch.setattr(email_provider.smtplib, "SMTP", Client)
    box = Mailbox("mail.example", 587, "starttls", "u", "p", "guard@example.org")
    email_provider.deliver(box, email_provider.compose(box.sender, "a@example.org", Reply("x")))
    assert calls == ["connect", "starttls", "login", "send"]


def test_email_settings_are_checked(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="plain email"):
        Settings(_env_file=None, data_dir=tmp_path, email_recipients="x y")  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="server"):
        Settings(_env_file=None, data_dir=tmp_path, email_enabled=True,  # type: ignore[call-arg]
                 telegram_enabled=False)  # fmt: skip


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd,
                          check=True, capture_output=True, text=True)  # fmt: skip
    return done.stdout.strip()


def _plugin_repo(path: Path, **extra: Any) -> str:
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    manifest = {"id": "doorbell", "name": "Doorbell", "version": "1", **extra}
    (path / "plugin.json").write_text(json.dumps(manifest))
    (path / "plugin.py").write_text("# one\n")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "one")
    return f"file://{path}"


class UpdaterApp:
    def __init__(self, db: Any, data_dir: Path) -> None:
        self.db = db
        self.settings = SimpleNamespace(data_dir=data_dir)
        self.notes: list[str] = []
        self.messenger = SimpleNamespace(notify_admins=self._note)

    async def _note(self, text: str) -> None:
        self.notes.append(text)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
async def test_git_plugins_update_only_if_chosen(
    db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plugin_updates, "PROTOCOL", "file")
    url = _plugin_repo(tmp_path / "repo")
    app = UpdaterApp(db, tmp_path / "data")
    updater = PluginUpdater(app)
    await updater.install(url)
    installed = updater.root / "doorbell" / "plugin.py"
    assert installed.read_text() == "# one\n" and not (updater.root / "doorbell" / ".git").exists()

    (tmp_path / "repo" / "plugin.py").write_text("# two\n")
    _git(tmp_path / "repo", "commit", "-qam", "two")
    await updater._pass()  # found, not installed: nobody asked for that
    record = updater.record("doorbell")
    assert record is not None and record["latest"] != record["commit"]
    assert installed.read_text() == "# one\n" and not app.notes

    assert updater.set_auto("doorbell", True)
    await updater._pass()
    assert installed.read_text() == "# two\n" and len(app.notes) == 1
    record = updater.record("doorbell")
    assert record is not None and record["latest"] == record["commit"]


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
async def test_git_plugins_follow_their_release_branch(
    db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plugin_updates, "PROTOCOL", "file")
    url = _plugin_repo(tmp_path / "repo", release_branch="stable")
    repo = tmp_path / "repo"
    _git(repo, "checkout", "-qb", "stable")
    (repo / "plugin.py").write_text("# stable\n")
    _git(repo, "commit", "-qam", "stable")
    _git(repo, "checkout", "-q", "main")
    updater = PluginUpdater(UpdaterApp(db, tmp_path / "data"))
    await updater.install(url)
    assert (updater.root / "doorbell" / "plugin.py").read_text() == "# stable\n"
    record = updater.record("doorbell")
    assert record is not None and record["branch"] == "stable"


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
async def test_a_repository_cannot_replace_another_plugin(
    db: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plugin_updates, "PROTOCOL", "file")
    repo = tmp_path / "repo"
    url = _plugin_repo(repo)
    updater = PluginUpdater(UpdaterApp(db, tmp_path / "data"))
    await updater.install(url)
    (repo / "plugin.json").write_text(json.dumps({"id": "other", "name": "Other"}))
    _git(repo, "commit", "-qam", "takeover")
    with pytest.raises(PluginGitError):
        await updater.update("doorbell")
    assert (updater.root / "doorbell" / "plugin.json").exists()
    assert not (updater.root / "other").exists()


def test_a_bad_release_branch_is_refused() -> None:
    with pytest.raises(ValueError, match="release_branch"):
        check_manifest({"id": "ab", "name": "x", "release_branch": "--upload-pack=evil"})


async def test_donation_addresses_are_shown_with_codes(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        await _login(client)
        coins = await (await client.get("/api/support/donate")).json()
        assert [c["id"] for c in coins] == ["monero", "bitcoin", "litecoin"]
        assert coins[1]["address"] == "3B8PdpJnmL7rQyHhBytKtvUT7NgQfraBEH"
        assert all(c["qr"].startswith("data:image/svg+xml") for c in coins)
    finally:
        await client.close()
        app.db.close()


async def _linking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, finish: Any) -> Any:
    from pi_guard.web import api_tools

    async def fake(address: str, method: str, params: Any = None, wait: float = 10) -> Any:
        if method == "startLink":
            return {"deviceLinkUri": "sgnl://linkdevice?uuid=a&pub_key=b"}
        if method == "finishLink":
            return await finish()
        return {"version": "0.14"} if method == "version" else []

    monkeypatch.setattr(api_tools, "rpc", fake)
    api_tools._links.clear()
    client, app = await _client(tmp_path)
    return client, app, {"X-CSRF-Token": await _login(client)}


async def _link_state(client: Any) -> Any:
    for _ in range(50):
        status = await (await client.get("/api/signal")).json()
        if status["link"].get("state") != "waiting":
            return status
        await asyncio.sleep(0.01)
    return status


async def test_linked_phone_brings_its_number(tmp_path: Path, monkeypatch: Any) -> None:
    async def finish() -> Any:
        await asyncio.sleep(0.02)
        return {"number": "+4915112345678"}

    client, app, headers = await _linking(tmp_path, monkeypatch, finish)
    try:
        answer = await client.post("/api/signal/link", json={}, headers=headers)
        code = await answer.json()
        assert code["qr"].startswith("data:image/svg") and 0 < code["seconds"] < 120
        status = await _link_state(client)
        assert status["link"]["state"] == "linked" and status["linked"]
        stored = (app.settings.data_dir / "dashboard.env").read_text()
        assert "+4915112345678" in stored and "PI_GUARD_SIGNAL_ENABLED=true" in stored
    finally:
        await client.close()
        app.db.close()


async def test_expired_code_is_not_a_failure(tmp_path: Path, monkeypatch: Any) -> None:
    async def finish() -> Any:
        raise RuntimeError("Link request timed out, please try again.")

    client, app, headers = await _linking(tmp_path, monkeypatch, finish)
    try:
        await client.post("/api/signal/link", json={}, headers=headers)
        assert (await _link_state(client))["link"]["state"] == "expired"
    finally:
        await client.close()
        app.db.close()


async def test_a_new_code_replaces_the_one_before(tmp_path: Path, monkeypatch: Any) -> None:
    from pi_guard.web import api_tools

    async def finish() -> Any:
        await asyncio.sleep(30)

    client, app, headers = await _linking(tmp_path, monkeypatch, finish)
    try:
        await client.post("/api/signal/link", json={}, headers=headers)
        first = api_tools._links["task"]
        await client.post("/api/signal/link", json={}, headers=headers)
        await asyncio.sleep(0)
        assert first.cancelled() or first.cancelling()
        assert (await (await client.get("/api/signal")).json())["link"]["state"] == "waiting"
        api_tools._links["task"].cancel()
    finally:
        await client.close()
        app.db.close()


async def test_success_shows_at_once_and_survives_a_late_renewal(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from pi_guard.web import api_tools

    async def finish() -> Any:
        return {"number": "+4915112345678"}

    client, app, headers = await _linking(tmp_path, monkeypatch, finish)
    try:
        await client.post("/api/signal/link", json={}, headers=headers)
        await asyncio.sleep(0.05)

        async def silent(*_: Any, **__: Any) -> Any:
            raise AssertionError("the state must not ask signal-cli, it is busy")

        monkeypatch.setattr(api_tools, "rpc", silent)
        state = await (await client.get("/api/signal/link")).json()
        assert state == {"state": "linked", "number": "+4915112345678"}
        again = await client.post("/api/signal/link", json={}, headers=headers)
        assert (await again.json()) == {"linked": True}
        assert api_tools._links["state"] == "linked"
    finally:
        api_tools._links.clear()
        await client.close()
        app.db.close()


async def test_a_busy_signal_cli_is_not_called_broken(tmp_path: Path, monkeypatch: Any) -> None:
    from pi_guard.web import api_tools

    async def stuck(*_: Any, **__: Any) -> Any:
        raise TimeoutError

    client, app, _ = await _linking(tmp_path, monkeypatch, stuck)
    monkeypatch.setattr(api_tools, "rpc", stuck)
    try:
        status = await (await client.get("/api/signal")).json()
        assert status["running"] and status["busy"]
    finally:
        await client.close()
        app.db.close()


async def test_a_test_message_goes_to_note_to_self(tmp_path: Path, monkeypatch: Any) -> None:
    from pi_guard.web import api_tools

    sent: list[Any] = []

    async def rpc(_: str, method: str, params: Any = None, wait: float = 0) -> Any:
        sent.append((method, params))

    client, app, headers = await _linking(tmp_path, monkeypatch, rpc)
    monkeypatch.setattr(api_tools, "rpc", rpc)
    api_tools._links.update(state="linked", number="+49151")
    try:
        answer = await client.post("/api/signal/test", json={}, headers=headers)
        assert answer.status == 200
        assert sent[0][0] == "send" and sent[0][1]["recipient"] == ["+49151"]
    finally:
        api_tools._links.clear()
        await client.close()
        app.db.close()


def test_discord_cards_take_their_title_from_the_first_line() -> None:
    from pi_guard.messaging.discord.provider import COLOURS, embed

    card = embed("Bewegung im Flur\nKamera: Flur", "alert")
    assert card["title"] == "Bewegung im Flur" and card["description"] == "Kamera: Flur"
    assert card["color"] == COLOURS["alert"]
    assert "title" not in embed("nur eine Zeile", "info")


def test_the_services_settings_beat_a_forgotten_env_file(tmp_path: Path, monkeypatch: Any) -> None:
    from pi_guard import config

    system = tmp_path / "pi-guard.env"
    system.write_text("")
    (tmp_path / ".env").write_text("")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PI_GUARD_ENV_FILE", raising=False)
    monkeypatch.setattr(config, "DEFAULT_ENV_FILE", system)
    assert config.find_env_file() == system
    assert not config.reads_other_settings(system)
    system.chmod(0)
    if not os.access(system, os.R_OK):  # root reads everything
        assert config.find_env_file() == Path(".env")
        assert config.reads_other_settings(Path(".env"))


def test_the_forecast_counts_the_last_day(tmp_path: Path) -> None:
    from pi_guard.diagnostics.storage import per_day

    new, old = tmp_path / "a.mp4", tmp_path / "b.mp4"
    new.write_bytes(b"x" * 10)
    old.write_bytes(b"x" * 99)
    os.utime(old, (1, 1))
    assert per_day(tmp_path, new.stat().st_mtime + 1) == 10
