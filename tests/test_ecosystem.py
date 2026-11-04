"""Connected computers, export and import, more languages and the add-on ecosystem."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from pi_guard import i18n
from pi_guard.addons import Addons
from pi_guard.addons import languages as languages_module
from pi_guard.addons.agents import AgentError, AgentHub, Connection
from pi_guard.addons.commander import Commander
from pi_guard.addons.languages import checked, protect, restore, translate_all
from pi_guard.addons.plugin import ManifestError, Plugin, check_manifest
from pi_guard.addons.remote import ConfirmationError
from pi_guard.config.files import FileConfig
from pi_guard.core.events import Event, EventType

from .helpers import make_app
from .test_web import PASSWORD, _client, _login


def enable(app: Any, **addons: Any) -> None:
    object.__setattr__(app.config, "file", FileConfig.model_validate({"addons": addons}))
    app.addons = Addons(app)


# --- computers -----------------------------------------------------------------------------


def test_agent_tokens(db: Any) -> None:
    hub = AgentHub(db)
    agent_id, token = hub.create("Arch PC")
    assert token.startswith("pga_") and hub.authenticate(token) == agent_id
    assert hub.authenticate("pga_wrong") is None
    assert token not in [r[0] for r in db.execute("SELECT token_hash FROM agents")]
    with pytest.raises(AgentError):
        hub.create("arch pc")
    hub.delete(agent_id)
    assert hub.authenticate(token) is None


async def test_requests_reach_the_agent_and_time_out(db: Any, monkeypatch: Any) -> None:
    from pi_guard.addons import agents

    hub = AgentHub(db)
    agent_id, _ = hub.create("PC")
    with pytest.raises(AgentError, match="offline"):
        await hub.request(agent_id, cmd="status")
    sent: list[dict[str, Any]] = []

    async def send(text: str) -> None:
        sent.append(json.loads(text))
        hub.received(
            connection, json.dumps({"type": "reply", "id": sent[-1]["id"], "ok": True, "data": 7})
        )

    connection = Connection(agent_id, send)
    hub.attach(connection)
    assert await hub.request(agent_id, cmd="docker", action="list") == 7

    async def silent(text: str) -> None:
        return None

    connection.send = silent
    monkeypatch.setattr(agents, "REQUEST_SECONDS", 0.01)
    with pytest.raises(AgentError, match="timeout"):
        await hub.request(agent_id, cmd="status")
    hub.received(connection, json.dumps({"type": "status", "data": {"os": "Arch Linux"}}))
    hub.received(connection, "not json")
    assert hub.list()[0]["info"]["os"] == "Arch Linux" and hub.list()[0]["online"]
    hub.detach(connection)
    assert not hub.list()[0]["online"]


def test_power_needs_two_confirmations(db: Any, monkeypatch: Any) -> None:
    from pi_guard.addons import agents

    monkeypatch.setattr(agents, "MIN_SECONDS_BETWEEN_STEPS", 0)
    hub = AgentHub(db)
    agent_id, _ = hub.create("PC")
    with pytest.raises(ConfirmationError):
        hub.begin(agent_id, "shutdown", "a")  # not connected
    hub.attach(Connection(agent_id, None))
    with pytest.raises(ConfirmationError):
        hub.begin(agent_id, "format-disk", "a")
    first = hub.begin(agent_id, "shutdown", "a")
    _, _, second = hub.confirm(first, "a")
    assert second
    with pytest.raises(ConfirmationError):
        hub.confirm(second, "b")
    token = hub.begin(agent_id, "shutdown", "a")
    _, _, token2 = hub.confirm(token, "a")
    assert hub.confirm(token2, "a") == (agent_id, "shutdown", None)


async def test_agent_socket_needs_a_token(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        enable(app, agents={"enabled": True})
        hub = app.addons.agents
        agent_id, token = hub.create("PC")
        assert (
            await client.get("/api/agent/ws", headers={"Authorization": "Bearer x"})
        ).status == 401
        socket = await client.ws_connect(
            "/api/agent/ws", headers={"Authorization": f"Bearer {token}"}
        )
        await socket.send_str(json.dumps({"type": "status", "data": {"os": "Arch"}}))
        await asyncio.sleep(0.05)
        assert hub.list()[0]["online"]
        task = asyncio.create_task(hub.docker(agent_id, "list"))
        request = await socket.receive_json()
        assert request["cmd"] == "docker"
        await socket.send_str(
            json.dumps({"type": "reply", "id": request["id"], "ok": True, "data": []})
        )
        assert await task == []
        await socket.close()
        await asyncio.sleep(0.05)
        assert not hub.list()[0]["online"]
    finally:
        await client.close()
        app.db.close()


def test_shut_down_my_pc_by_asking(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    enable(app, assistant={"enabled": True}, agents={"enabled": True})
    agent_id, _ = app.addons.agents.create("Arch PC")
    proposal = Commander(app).understand("Fahre meinen PC herunter")
    assert proposal is not None and proposal.action == "agent_power"
    assert proposal.args == {"agent": agent_id, "command": "shutdown"}
    app.db.close()


# --- export and import ---------------------------------------------------------------------


async def test_export_formats_and_secrets(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        h = {"X-CSRF-Token": await _login(client)}
        app.db.audit("=cmd|calc", "login")

        async def export(fmt: str) -> Any:
            return await client.post("/api/export/data", json={"format": fmt, "password": PASSWORD},
                                     headers=h)  # fmt: skip

        wrong = await client.post(
            "/api/export/data", json={"format": "json", "password": "x"}, headers=h
        )
        assert wrong.status == 403
        data = json.loads(await (await export("json")).read())
        assert {"events", "audit", "users", "cameras"} <= set(data)
        assert "password_hash" not in json.dumps(data)
        assert "== audit" in await (await export("txt")).text()
        bundle = zipfile.ZipFile(io.BytesIO(await (await export("csv")).read()))
        rows = list(csv.DictReader(io.StringIO(bundle.read("audit.csv").decode())))
        assert any(r["actor"] == "'=cmd|calc" for r in rows)  # no formula in spreadsheets
    finally:
        await client.close()
        app.db.close()


async def test_config_round_trip(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        h = {"X-CSRF-Token": await _login(client)}
        response = await client.post("/api/export/config", json={"password": PASSWORD}, headers=h)
        config = json.loads(await response.read())
        assert config["pi_guard_config"] == 1 and config["contains_secrets"] is False
        assert "telegram_bot_token" not in config["settings"]
        config["settings"]["recording_retention_days"] = 21
        ok = await client.post(
            "/api/import/config", json={"config": config, "password": PASSWORD}, headers=h
        )
        assert ok.status == 200
        assert (
            "PI_GUARD_RECORDING_RETENTION_DAYS=21"
            in (app.settings.data_dir / "dashboard.env").read_text()
        )
        body = {"password": PASSWORD, "secrets": True}
        with_secrets = await client.post("/api/export/config", json=body, headers=h)
        assert json.loads(await with_secrets.read())["settings"]["telegram_bot_token"]
        broken = {**config, "config": {"cameras": [{"id": "BAD ID"}]}}
        response = await client.post(
            "/api/import/config", json={"config": broken, "password": PASSWORD}, headers=h
        )
        assert response.status == 400
        assert (
            await client.post(
                "/api/import/config", json={"config": {}, "password": PASSWORD}, headers=h
            )
        ).status == 400
        no = await client.post(
            "/api/import/config", json={"config": config, "password": "x"}, headers=h
        )
        assert no.status == 403
    finally:
        await client.close()
        app.db.close()


# --- languages -----------------------------------------------------------------------------


def test_placeholders_survive_translation() -> None:
    text = "{name} & co <b>"
    assert protect(text) == "<x>{name}</x> &amp; co &lt;b>"
    assert restore("<x>{name}</x> &amp; cie &lt;b>") == "{name} & cie <b>"
    assert checked("Hi {name}", "Salut {name}") == "Salut {name}"
    assert checked("Hi {name}", "Salut {nom}") == "Hi {name}"  # would crash at runtime


async def test_translation_is_stored_and_usable(tmp_path: Path, monkeypatch: Any) -> None:
    calls: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(
            200, json={"translations": [{"text": f"FR {t}"} for t in body["text"]]}
        )

    real = httpx.AsyncClient
    monkeypatch.setattr(languages_module.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handle), **kw))  # fmt: skip
    seen: list[int] = []
    count = await translate_all("fr", "key:fx", tmp_path, lambda d, t: seen.append(d))
    try:
        assert count == len(i18n.catalog("en")) and seen[-1] == count
        assert all(len(c["text"]) <= 50 for c in calls)
        assert calls[0]["target_lang"] == "FR" and calls[0]["ignore_tags"] == ["x"]
        assert "fr" in i18n.LANGUAGES
        assert i18n.t("web.login", "fr").startswith("FR ")
        assert i18n.t("assistant.time", "fr", time="12:00").endswith("12:00.")
    finally:
        i18n.remove_extra("fr")
    assert "fr" not in i18n.LANGUAGES


# --- add-on ecosystem ----------------------------------------------------------------------


def test_manifest_fields() -> None:
    manifest = check_manifest({
        "id": "doorbell", "name": "Doorbell", "version": "1.2", "icon": "bell",
        "authors": ["A", "B"],
        "source": "https://github.com/x/y", "license": "MIT", "permissions": ["audio"],
        "settings": [{"key": "volume", "label": "Volume", "type": "number", "default": 40},
                     {"key": "BAD KEY"}],
    })  # fmt: skip
    assert manifest["authors"] == ["A", "B"] and manifest["icon"] == "bell"
    assert [f["key"] for f in manifest["settings"]] == ["volume"]
    assert check_manifest({"id": "ab", "name": "x", "author": "One"})["authors"] == ["One"]
    for bad in ({"id": "A B", "name": "x"}, {"id": "remote", "name": "x"}, {"id": "ab"},
                {"id": "ab", "name": "x", "entry": "../x.py"},
                {"id": "ab", "name": "x", "source": "git://x"},
                {"id": "ab", "name": "x", "api": 99}):  # fmt: skip
        with pytest.raises(ManifestError):
            check_manifest(bad)


async def test_plugin_sdk(tmp_path: Path) -> None:
    app = make_app(tmp_path)

    class Counter(Plugin):
        id = "counter"
        seen: list[Any] = []  # noqa: RUF012

        def events(self) -> list[str]:
            return ["motion_detected"]

        async def on_event(self, event: Event) -> None:
            self.save("count", self.load("count", 0) + 1)
            self.seen.append(event.type)

    plugin = Counter(app, {})
    app.addons.plugins = [plugin]
    components = app.addons.components()
    task = asyncio.create_task(components["plugin-events:counter"]())
    app.bus.publish(Event(EventType.MODE_CHANGED, data={"mode": "HOME"}))
    app.bus.publish(Event(EventType.MOTION_DETECTED, camera="a"))
    await asyncio.sleep(0.05)
    task.cancel()
    assert plugin.seen == [EventType.MOTION_DETECTED] and plugin.load("count") == 1
    await plugin.notify("Hallo")
    assert app.providers["telegram"].last().text == "Hallo"
    app.db.close()


def test_developer_tools(tmp_path: Path, monkeypatch: Any) -> None:
    from pi_guard.cli import app as cli

    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    assert runner.invoke(cli, ["plugin", "new", "my_addon"]).exit_code == 0
    assert "OK" in runner.invoke(cli, ["plugin", "check", "my_addon"]).output
    assert runner.invoke(cli, ["plugin", "pack", "my_addon"]).exit_code == 0
    names = zipfile.ZipFile(tmp_path / "my_addon-0.1.0.zip").namelist()
    assert "plugin.json" in names and "plugin.py" in names
    (tmp_path / "my_addon" / "plugin.py").write_text("x = 1\n")
    assert runner.invoke(cli, ["plugin", "check", "my_addon"]).exit_code == 1


async def test_git_links_must_be_https(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        h = {"X-CSRF-Token": await _login(client)}
        for url in ("file:///etc", "ext::sh -c id", "git://x/y", "https://x/y;id"):
            body = {"url": url, "password": PASSWORD}
            response = await client.post("/api/plugin-manager/git", json=body, headers=h)
            assert response.status == 400
    finally:
        await client.close()
        app.db.close()
