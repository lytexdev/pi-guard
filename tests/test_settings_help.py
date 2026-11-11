"""Asking about settings, proposals that are checked, and undoing any change."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pi_guard.addons import settings_help
from pi_guard.config import read_overrides

from .test_web import _client, _login


class FakeAssistant:
    ready = True

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.system = ""

    async def complete_checked(self, system: str, question: str) -> tuple[str, int]:
        self.system = system
        return self.reply, 42


async def _app(tmp_path: Path, reply: str | None = None) -> tuple[Any, Any, dict[str, str], Any]:
    client, app = await _client(tmp_path)
    headers = {"X-CSRF-Token": await _login(client)}
    fake = FakeAssistant(reply) if reply is not None else None
    app.addons = SimpleNamespace(assistant=fake)
    return client, app, headers, fake


async def test_without_the_ai_settings_are_found_by_their_words(tmp_path: Path) -> None:
    client, app, headers, _ = await _app(tmp_path)
    try:
        body = {"question": "How long are recordings kept?"}
        data = await (await client.post("/api/settings/ask", json=body, headers=headers)).json()
        assert data["ai"] is False and data["proposal"] is None
        assert "recording_retention_days" in [f["key"] for f in data["found"]]
    finally:
        await client.close()
        app.db.close()


async def test_a_proposal_is_checked_and_changes_nothing_by_itself(tmp_path: Path) -> None:
    reply = 'Recordings stay 30 days.\nCHANGE {"key": "recording_retention_days", "value": 14}'
    client, app, headers, fake = await _app(tmp_path, reply)
    try:
        body = {"question": "keep recordings for 14 days"}
        data = await (await client.post("/api/settings/ask", json=body, headers=headers)).json()
        assert data["answer"] == "Recordings stay 30 days."
        assert data["proposal"]["key"] == "recording_retention_days"
        assert data["proposal"]["after"] == 14
        assert "recording_retention_days" not in read_overrides(app.settings.data_dir)
        assert "telegram_bot_token" not in fake.system  # secrets are not even named
    finally:
        await client.close()
        app.db.close()


def test_proposals_outside_the_rules_are_dropped() -> None:
    settings = SimpleNamespace(recording_retention_days=30, motion_sensitivity=50)
    entries = settings_help.catalog(settings, "en")
    for line in (
        'CHANGE {"key": "dashboard_auth", "value": ["none"]}',  # who gets in: never
        'CHANGE {"key": "assistant_provider", "value": "ollama"}',
        'CHANGE {"key": "motion_sensitivity", "value": 500}',  # out of range
        'CHANGE {"key": "invented_key", "value": 1}',
        'CHANGE {"key": "telegram_admin_user_ids", "value": "1"}',  # lists are never proposed
        "CHANGE {not json}",
    ):
        assert settings_help.proposal_from(line, entries, "en") is None, line
    good = 'CHANGE {"key": "motion_sensitivity", "value": "70"}'
    assert settings_help.proposal_from(good, entries, "en")["after"] == 70


def test_the_ai_never_sees_secrets_lists_or_the_place() -> None:
    settings = SimpleNamespace(timezone="Europe/Berlin", telegram_admin_user_ids=[123456],
                               motion_sensitivity=50)  # fmt: skip
    public = {e.key: e.public() for e in settings_help.catalog(settings, "en")}
    assert "telegram_bot_token" not in public
    assert "value" not in public["timezone"]
    assert "value" not in public["telegram_admin_user_ids"]
    assert public["motion_sensitivity"]["value"] == 50


async def test_any_change_can_be_undone(tmp_path: Path) -> None:
    client, app, headers, _ = await _app(tmp_path)
    try:
        put = client.put
        await put("/api/settings", json={"motion_sensitivity": 70}, headers=headers)
        await put("/api/settings?source=assistant", json={"motion_sensitivity": 20,
                                                          "recording_retention_days": 9},
                  headers=headers)  # fmt: skip
        history = await (await client.get("/api/settings/history", headers=headers)).json()
        newest = history[0]
        assert newest["source"] == "assistant"
        assert newest["changes"]["motion_sensitivity"] == ["70", "20"]
        assert newest["changes"]["recording_retention_days"] == [None, "9"]
        url = f"/api/settings/history/{newest['id']}/revert"
        assert (await client.post(url, headers=headers)).status == 200
        overrides = read_overrides(app.settings.data_dir)
        assert overrides["motion_sensitivity"] == "70"
        assert "recording_retention_days" not in overrides  # back to pi-guard.env
        history = await (await client.get("/api/settings/history", headers=headers)).json()
        assert history[0]["source"] == "revert"
    finally:
        await client.close()
        app.db.close()


async def test_secrets_are_not_kept_in_the_history(tmp_path: Path) -> None:
    client, app, headers, _ = await _app(tmp_path)
    try:
        body = {"email_password": "very-secret-1"}
        assert (await client.put("/api/settings", json=body, headers=headers)).status == 200
        history = await (await client.get("/api/settings/history", headers=headers)).json()
        assert history[0]["changes"] == {"email_password": None}
        rows = app.db.execute("SELECT changes FROM settings_history").fetchall()
        assert all("very-secret" not in r[0] for r in rows)
        url = f"/api/settings/history/{history[0]['id']}/revert"
        assert (await client.post(url, headers=headers)).status == 409
    finally:
        await client.close()
        app.db.close()
