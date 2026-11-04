"""Signal waiting, Wi-Fi codes, weekly plan, support, navigation, scan and storage."""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from pi_guard.core.modes import Mode, ModeController, Presence, Selection
from pi_guard.core.schedule import PLAN_KEY, ScheduleRunner, clean_plan
from pi_guard.diagnostics import scan, storage
from pi_guard.messaging.signal.provider import SignalProvider, parse_envelope
from pi_guard.network.fritzbox import FritzBox, FritzError, Wlan
from pi_guard.web.api_tools import _wifi_text

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
