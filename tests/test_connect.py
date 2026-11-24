"""Computer-Connect: fixed actions to computers with the agent, files passed through in pieces."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import aiohttp

from pi_guard.addons.agents import AgentHub, Connection
from pi_guard.federation.protocol import tunnel_allowed
from pi_guard.web import permissions

from .test_web import _client, _login


class FakeAgent:
    """Answers like agent/src/connect.rs, keeping what arrives."""

    def __init__(self, hub: AgentHub, agent_id: int, allow: bool = True) -> None:
        self.hub, self.allow = hub, allow
        self.requests: list[dict[str, Any]] = []
        self.received = b""
        self.connection = Connection(agent_id, self.send)
        hub.attach(self.connection)

    async def send(self, text: str) -> None:
        request = json.loads(text)
        self.requests.append(request)
        ok, data = True, self.answer(request)
        if not self.allow:
            ok, data = False, f"{request['cmd']} is not allowed on this computer"
        self.hub.received(self.connection, json.dumps({"type": "reply", "id": request["id"],
                                                        "ok": ok, "data": data}))  # fmt: skip

    def answer(self, request: dict[str, Any]) -> Any:
        if request["cmd"] == "media":
            return {"status": "playing", "title": "Song", "player": "org.mpris.MediaPlayer2.x"}
        if request["cmd"] == "file" and request["action"] == "chunk":
            self.received += base64.b64decode(request["data"])
            return len(self.received)
        if request["cmd"] == "file" and request["action"] == "end":
            assert request["sha256"] == hashlib.sha256(self.received).hexdigest()
            return {"saved": "/home/anna/Downloads/pi-guard/x.bin"}
        return True


async def _setup(tmp_path: Path, allow: bool = True) -> tuple[Any, Any, dict[str, str], FakeAgent]:
    client, app = await _client(tmp_path)
    app.addons.agents = AgentHub(app.db)
    agent_id, _ = app.addons.agents.create("Arch PC")
    agent = FakeAgent(app.addons.agents, agent_id, allow)
    headers = {"X-CSRF-Token": await _login(client)}
    return client, app, headers, agent


async def test_media_ring_and_text_are_fixed_actions(tmp_path: Path) -> None:
    client, app, headers, agent = await _setup(tmp_path)
    try:
        rows = await (await client.get("/api/connect")).json()
        assert rows[0]["name"] == "Arch PC" and rows[0]["connect"] is None  # not reported yet
        url = f"/api/connect/{rows[0]['id']}"
        playing = await client.post(f"{url}/media", json={"action": "next"}, headers=headers)
        assert playing.status == 200 and (await playing.json())["title"] == "Song"
        assert (
            await client.post(f"{url}/media", json={"action": "rm -rf /"}, headers=headers)
        ).status == 400
        bad = {"action": "next", "player": "../../x"}
        assert (await client.post(f"{url}/media", json=bad, headers=headers)).status == 400
        assert (await client.post(f"{url}/ring", headers=headers)).status == 200
        assert agent.requests[-1]["note"]  # in the owner's language
        assert (await client.delete(f"{url}/ring", headers=headers)).status == 200
        assert agent.requests[-1]["cmd"] == "ring_stop"  # never "ring": an older agent would ring
        rows = await (await client.get("/api/connect")).json()
        assert rows[0]["can_power"] is False  # not reported yet
        assert (await client.post(f"{url}/text", json={"text": " "}, headers=headers)).status == 400
        sent = await client.post(f"{url}/text", json={"text": "hi", "copy": True}, headers=headers)
        assert sent.status == 200 and agent.requests[-1] == {
            **agent.requests[-1],
            "text": "hi",
            "copy": True,
        }
        actions = [r["action"] for r in app.db.execute("SELECT action FROM audit").fetchall()]
        assert {"connect.media", "connect.ring", "connect.text"} <= set(actions)
    finally:
        await client.close()
        app.db.close()


async def test_a_file_passes_through_in_pieces(tmp_path: Path, monkeypatch: Any) -> None:
    from pi_guard.web import api_connect

    monkeypatch.setattr(api_connect, "CHUNK", 1000)
    client, app, headers, agent = await _setup(tmp_path)
    try:
        url = f"/api/connect/{agent.connection.agent_id}/file"
        content = bytes(range(256)) * 20
        form = aiohttp.FormData()
        form.add_field("size", str(len(content)))
        form.add_field("file", content, filename="../x.bin")
        response = await client.post(url, data=form, headers=headers)
        assert response.status == 200 and (await response.json())["bytes"] == len(content)
        assert agent.received == content
        chunks = [r for r in agent.requests if r.get("action") == "chunk"]
        assert len(chunks) == 6 and "x.bin" in agent.requests[0]["name"]  # the agent cleans it
        assert not list(tmp_path.rglob("x.bin"))  # nothing kept on the Pi

        lying = aiohttp.FormData()
        lying.add_field("size", "10")
        lying.add_field("file", content, filename="y.bin")
        assert (await client.post(url, data=lying, headers=headers)).status == 400
        assert agent.requests[-1]["action"] == "abort"
        too_big = aiohttp.FormData()
        too_big.add_field("size", str(api_connect.MAX_FILE + 1))
        too_big.add_field("file", b"x", filename="z.bin")
        assert (await client.post(url, data=too_big, headers=headers)).status == 413
    finally:
        await client.close()
        app.db.close()


async def test_a_computer_that_allows_nothing_says_so(tmp_path: Path) -> None:
    client, app, headers, agent = await _setup(tmp_path, allow=False)
    try:
        url = f"/api/connect/{agent.connection.agent_id}/ring"
        response = await client.post(url, headers=headers)
        assert response.status == 502 and "not allowed" in await response.text()
    finally:
        await client.close()
        app.db.close()


def test_connect_is_granted_with_its_add_on_and_never_through_a_household() -> None:
    assert permissions.allowed(["addon:remote"], "POST", "/api/connect/1/media")
    assert not permissions.allowed(["addon:agents"], "POST", "/api/connect/1/file")
    assert not tunnel_allowed("/api/connect/1/file", "admin", [])
