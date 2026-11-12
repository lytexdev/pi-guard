"""Network management, API tokens, own add-ons and doing things by asking."""

from __future__ import annotations

import asyncio
import io
import json
import shutil
import ssl
import zipfile
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from pi_guard.addons.analytics import hints
from pi_guard.addons.blocking import BlockError, Blocks, clean_domain, rule
from pi_guard.addons.blockpage import BlockPage, render
from pi_guard.addons.commander import Commander, Proposal
from pi_guard.addons.jobs import Jobs, next_time
from pi_guard.addons.remote import RemoteActions
from pi_guard.addons.smarthome import Live
from pi_guard.config.files import RemoteAction, SmartDevice
from pi_guard.detection.faces import BiometricStore
from pi_guard.network import fritzbox
from pi_guard.network.fritzbox import FritzBox

from .helpers import ADMIN, VIEWER, make_app, msg
from .test_web import PASSWORD, _client, _login

# --- blocking ------------------------------------------------------------------------------


class Hole:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.groups: list[dict[str, Any]] = [{"id": 0, "name": "Default"}]
        self.clients: list[dict[str, Any]] = []

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        body = kwargs.get("json")
        self.calls.append((method, path, body))
        if path == "/api/groups" and method == "POST":
            self.groups.append({"id": 7, "name": body["name"]})
        if path == "/api/groups":
            return {"groups": self.groups}
        if path == "/api/clients" and method == "GET":
            return {"clients": self.clients}
        return {}


def test_domains_are_cleaned() -> None:
    assert clean_domain(" https://www.TikTok.com/foryou ") == "tiktok.com"
    assert rule("tiktok.com") == r"(\.|^)tiktok\.com$"
    for bad in ("", "nur-text", "evil.com$|.*", "http://"):
        with pytest.raises(BlockError):
            clean_domain(bad)


async def test_blocks_for_everyone_and_temporary_ones_end(db: Any) -> None:
    hole = Hole()
    blocks = Blocks(db, hole)  # type: ignore[arg-type]
    await blocks.add("tiktok.com", minutes=30)
    assert hole.calls[-1][2]["domain"] == r"(\.|^)tiktok\.com$"
    assert hole.calls[-1][2]["groups"] == [0]
    row = blocks.list()[0]
    assert row["until"]
    db.execute("UPDATE net_blocks SET until = '2000-01-01T00:00:00+00:00'")
    task = asyncio.create_task(blocks.run())
    await asyncio.sleep(0.05)
    task.cancel()
    assert blocks.list() == []
    assert hole.calls[-1][0] == "DELETE"
    with pytest.raises(BlockError):
        await blocks.add("example.org", minutes=99999999)


async def test_block_for_one_device_uses_its_own_group(db: Any) -> None:
    hole = Hole()
    blocks = Blocks(db, hole)  # type: ignore[arg-type]
    await blocks.add("roblox.com", device="192.168.178.40", label="Ben")
    assert hole.calls[-1][2]["groups"] == [7]
    client = next(c for c in hole.calls if c[1] == "/api/clients" and c[0] == "POST")
    assert client[2] == {"client": "192.168.178.40", "comment": "pi-guard", "groups": [0, 7]}


async def test_block_page_entry_only_with_a_page_address(db: Any) -> None:
    hole = Hole()
    blocks = Blocks(db, hole, "192.168.178.110")  # type: ignore[arg-type]
    await blocks.add("example.org")
    assert any("/api/config/dns/hosts/" in c[1] for c in hole.calls)


def test_block_page_text_is_escaped() -> None:
    page = render("<script>alert(1)</script>:80", "de")
    assert "<script>alert" not in page and "gesperrt" in page


@pytest.mark.skipif(not shutil.which("openssl"), reason="needs openssl")
async def test_block_page_serves_https_for_any_name(tmp_path: Path) -> None:
    import subprocess

    from pi_guard.addons import blockpage as module

    subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt",
                    "ec_paramgen_curve:prime256v1", "-nodes", "-keyout", str(tmp_path / "ca.key"),
                    "-out", str(tmp_path / "ca.crt"), "-subj", "/CN=test", "-days", "2"],
                   check=True, capture_output=True)  # fmt: skip
    from .test_addons import free_port

    module.HTTP_PORT, module.HTTPS_PORT = free_port(), free_port()
    task = asyncio.create_task(BlockPage(tmp_path, "de").run())
    await asyncio.sleep(0.5)
    try:
        context = ssl.create_default_context(cafile=str(tmp_path / "ca.crt"))
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", module.HTTPS_PORT, ssl=context, server_hostname="tiktok.example"
        )
        writer.write(b"GET / HTTP/1.0\r\nHost: tiktok.example\r\n\r\n")
        await writer.drain()
        answer = await reader.read()
        writer.close()
        assert b"403" in answer.split(b"\r\n")[0] and b"tiktok.example" in answer
    finally:
        task.cancel()


# --- router and device recognition ---------------------------------------------------------


async def test_fritzbox_wlans_and_hosts(monkeypatch: Any) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login_sid.lua":
            return httpx.Response(200, text='<Users><User last="1">fritz1234</User></Users>')
        if request.url.path == "/hosts.lua":
            return httpx.Response(
                200,
                text="""<List><Item><MACAddress>AA:BB:CC:00:00:01</MACAddress>
<IPAddress>192.168.178.40</IPAddress><HostName>Galaxy-von-Ben</HostName><Active>1</Active>
<InterfaceType>802.11</InterfaceType><X_AVM-DE_Speed>433</X_AVM-DE_Speed></Item></List>""",
            )
        action = request.headers["SoapAction"].split("#")[1]
        if action == "GetInfo":
            info = "<NewSSID>Gast</NewSSID><NewEnable>1</NewEnable><NewChannel>6</NewChannel>"
            return httpx.Response(200, text=info + "<NewStandard>n</NewStandard>")
        if action == "GetTotalAssociations":
            return httpx.Response(200, text="<NewTotalAssociations>2</NewTotalAssociations>")
        return httpx.Response(
            200, text="<NewX_AVM-DE_HostListPath>/hosts.lua</NewX_AVM-DE_HostListPath>"
        )

    real = httpx.AsyncClient
    monkeypatch.setattr(fritzbox.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handle), **kw))  # fmt: skip
    box = FritzBox("fritz.box", "", "x")
    wlans = await box.wlans()
    assert len(wlans) == 3 and wlans[2].guest and wlans[2].clients == 2
    hosts = await box.hosts()
    assert hosts[0].name == "Galaxy-von-Ben" and hosts[0].mac == "aa:bb:cc:00:00:01"


def test_devices_are_recognised_by_their_domains() -> None:
    domains = ["gateway.icloud.com", "p1.icloud.com", "api.roblox.com", "i.instagram.com"]
    assert hints(domains)[0] == "Apple" and "Roblox" in hints(domains)


# --- API tokens ----------------------------------------------------------------------------


async def test_api_tokens(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        csrf = {"X-CSRF-Token": await _login(client)}

        async def token(scope: str) -> str:
            body = {"name": scope, "scope": scope, "password": PASSWORD}
            response = await client.post("/api/tokens", json=body, headers=csrf)
            return str((await response.json())["token"])

        read, control = await token("read"), await token("control")
        anonymous = client.session.cookie_jar
        anonymous.clear()
        assert (await client.get("/api/v1/status")).status == 401
        bad = {"Authorization": "Bearer pg_wrong"}
        assert (await client.get("/api/v1/status", headers=bad)).status == 401
        r = {"Authorization": f"Bearer {read}"}
        c = {"Authorization": f"Bearer {control}"}
        assert (await (await client.get("/api/v1/status", headers=r)).json())["mode"]
        assert (await client.get("/api/v1/cameras", headers=r)).status == 200
        assert (await client.post("/api/v1/mode", json={"mode": "AWAY"}, headers=r)).status == 403
        response = await client.post("/api/v1/mode", json={"mode": "AWAY"}, headers=c)
        assert (await response.json())["mode"] == "AWAY"
        # A token only opens the API, not the dashboard.
        assert (await client.get("/api/settings", headers=c)).status == 401
        stored = [row[0] for row in app.db.execute("SELECT hash FROM web_tokens")]
        assert control not in stored
        spec = await (await client.get("/api/v1/openapi.json", headers=r)).json()
        assert "/api/v1/cameras" in spec["paths"]
    finally:
        await client.close()
        app.db.close()


# --- own add-ons ---------------------------------------------------------------------------


def archive(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, text in files.items():
            bundle.writestr(name, text)
    return buffer.getvalue()


MANIFEST = json.dumps({"id": "lamp", "name": "Lamp", "version": "1.0", "entry": "plugin.py"})
CODE = """from pi_guard.addons.plugin import Plugin
class Lamp(Plugin):
    id = "lamp"
"""


async def upload(
    client: Any, headers: dict[str, str], data: bytes, password: str = PASSWORD
) -> Any:
    import aiohttp

    form = aiohttp.FormData()
    form.add_field("password", password)
    form.add_field("file", data, filename="a.zip")
    return await client.post("/api/plugin-manager", data=form, headers=headers)


async def test_add_on_upload_is_checked(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        h = {"X-CSRF-Token": await _login(client)}
        good = archive({"plugin.json": MANIFEST, "plugin.py": CODE})
        assert (await upload(client, h, good, "wrong")).status == 403
        evil = archive({"plugin.json": MANIFEST, "plugin.py": CODE, "../../evil.py": "x"})
        assert (await upload(client, h, evil)).status == 400
        assert (await upload(client, h, b"not a zip")).status == 400
        assert (await upload(client, h, archive({"plugin.py": CODE}))).status == 400
        reserved = json.dumps({"id": "remote", "name": "x", "entry": "plugin.py"})
        assert (
            await upload(client, h, archive({"plugin.json": reserved, "plugin.py": CODE}))
        ).status == 400
        assert not list((tmp_path).rglob("evil.py"))
        response = await upload(client, h, good)
        assert response.status == 200 and (await response.json())["sha256"]
        rows = await (await client.get("/api/plugin-manager")).json()
        assert rows[0]["id"] == "lamp" and rows[0]["enabled"] is False
        await client.put("/api/plugin-manager/lamp/enabled", json={"enabled": True}, headers=h)
        rows = await (await client.get("/api/plugin-manager")).json()
        assert rows[0]["enabled"] is True
        source = {"source": "def broken(:", "password": PASSWORD}
        assert (
            await client.put("/api/plugin-manager/lamp/source", json=source, headers=h)
        ).status == 400
        assert (await client.delete("/api/plugin-manager/lamp", headers=h)).status == 200
        assert await (await client.get("/api/plugin-manager")).json() == []
    finally:
        await client.close()
        app.db.close()


async def test_pinned_add_ons(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        h = {"X-CSRF-Token": await _login(client)}
        await client.put(
            "/api/navigation/pinned", json={"pinned": ["heartbeat", "bad id!"]}, headers=h
        )
        assert (await (await client.get("/api/session")).json())["pinned"] == ["heartbeat"]
    finally:
        await client.close()
        app.db.close()


# --- updates -------------------------------------------------------------------------------


async def test_update_only_leaves_a_request(tmp_path: Path, monkeypatch: Any) -> None:
    from pi_guard.web import api_system

    async def remote(*_: Any) -> dict[str, str]:
        return {"commit": "abc", "date": "2026-11-01T00:00:00Z", "message": "x"}

    monkeypatch.setattr(api_system, "_remote", remote)
    client, app = await _client(tmp_path)
    try:
        h = {"X-CSRF-Token": await _login(client)}
        status = await (await client.get("/api/system/update")).json()
        assert status["remote"]["commit"] == "abc" and status["pending"] is False
        assert status["instance"] and status["source"]["repository"] == "lytexdev/pi-guard"
        assert (await client.post("/api/system/update", headers=h)).status == 200
        assert (app.settings.data_dir / "update" / "request").exists()
    finally:
        await client.close()
        app.db.close()


# --- faces ---------------------------------------------------------------------------------


def test_person_details_show_what_is_stored(tmp_path: Path) -> None:
    import numpy as np

    store = BiometricStore(tmp_path / "b.db")
    vector = np.ones(128, dtype=np.float32)
    store.add("Ben", vector, "web:admin", 10)
    other = vector.copy()
    other[:64] = -1
    store.add("Ben", other, "web:admin", 10)
    rows = store.details("ben")
    assert len(rows) == 2 and rows[0]["numbers"] == 128 and rows[0]["bytes"] == 512
    assert store.delete_sample(rows[1]["id"]) and len(store.details("Ben")) == 1
    store.close()


# --- doing things by asking ----------------------------------------------------------------


def smart_app(tmp_path: Path) -> Any:
    from pi_guard.config.files import FileConfig

    app = make_app(tmp_path)
    object.__setattr__(app.config, "file", FileConfig.model_validate(
        {"addons": {"assistant": {"enabled": True}}}))  # fmt: skip
    from pi_guard.addons import Addons

    app.addons = Addons(app)

    class Home:
        connected = True
        live = {  # noqa: RUF012
            "schlaf": Live(SmartDevice(id="schlaf", name="Licht Schlafzimmer", command_topic="a")),
            "wohn": Live(SmartDevice(id="wohn", name="Licht Wohnzimmer", command_topic="b")),
            "spuel": Live(SmartDevice(id="spuel", name="Spülmaschine Restzeit", kind="sensor",
                                      state_topic="c", unit="min"), value="42"),
        }  # fmt: skip
        switched: list[tuple[str, bool]] = []  # noqa: RUF012

        def find(self, text: str) -> Any:
            return next(
                (i for i in self.live.values() if text.casefold() in i.device.name.casefold()), None
            )

        async def switch(self, device: str, on: bool) -> None:
            self.switched.append((device, on))

    app.addons.smarthome = Home()
    action = RemoteAction(id="pc", name="PC", host="h", user="u", command="c")
    app.addons.remote = RemoteActions([action], tmp_path)
    return app


@pytest.mark.parametrize(("text", "action", "args"), [
    ("Mach bitte das Licht im Schlafzimmer aus", "switch", {"device": "schlaf", "on": False}),
    ("Licht Wohnzimmer an", "switch", {"device": "wohn", "on": True}),
    ("Fahre meinen PC herunter", "remote", {"name": "pc"}),
    ("Spiel den Alarm ab", "alarm", {}),
    ("Alarm aus", "alarm_stop", {}),
    ("Nimm bitte heute Nacht von 3 bis 4 Uhr eine Mikrofonaufnahme auf", "record",
     {"start": "03:00", "end": "04:00"}),
    ("wie lange geht noch die Spülmaschine?", "state", {"device": "spuel"}),
    ("Erstelle einen VPN Zugang für Anna", "vpn", {"name": "Anna"}),
    ("Ich bin weg", "mode", {"mode": "AWAY"}),
])  # fmt: skip
def test_everyday_sentences(tmp_path: Path, text: str, action: str, args: dict[str, Any]) -> None:
    app = smart_app(tmp_path)
    proposal = Commander(app).understand(text)
    assert proposal is not None and proposal.action == action
    assert {k: proposal.args[k] for k in args} == args
    app.db.close()


def test_ten_minutes_of_video(tmp_path: Path) -> None:
    app = smart_app(tmp_path)
    proposal = Commander(app).understand("Nimm 10 Minuten Video auf und schick es mir")
    assert proposal is not None and proposal.action == "video"
    assert proposal.args["minutes"] == 10 and proposal.args["camera"]
    assert Commander(app).understand("Wie heißt die Hauptstadt von Frankreich?") is None
    app.db.close()


async def test_nothing_happens_without_confirmation(tmp_path: Path) -> None:
    app = smart_app(tmp_path)
    commander = app.addons.commander
    outcome = await commander.propose("Licht Schlafzimmer aus", "de", "telegram:1")
    assert outcome.pending and "Licht Schlafzimmer" in outcome.text
    assert app.addons.smarthome.switched == []

    async def deliver(path: str) -> None:
        return None

    no = await commander.answer(outcome.pending, False, "de", "telegram:1", deliver, {})
    assert app.addons.smarthome.switched == [] and no.pending is None
    outcome = await commander.propose("Licht Schlafzimmer aus", "de", "telegram:1")
    other = await commander.answer(outcome.pending, True, "de", "telegram:2", deliver, {})
    assert app.addons.smarthome.switched == [] and "abgelaufen" in other.text
    outcome = await commander.propose("Licht Schlafzimmer aus", "de", "telegram:1")
    await commander.answer(outcome.pending, True, "de", "telegram:1", deliver, {})
    assert app.addons.smarthome.switched == [("schlaf", False)]
    app.db.close()


async def test_remote_commands_need_a_second_yes(tmp_path: Path) -> None:
    from pi_guard.addons.remote import RunResult

    app = smart_app(tmp_path)
    ran: list[str] = []

    async def run(action: Any) -> RunResult:
        ran.append(action.id)
        return RunResult(True, 0, "")

    app.addons.remote.run = run
    commander = app.addons.commander

    async def deliver(path: str) -> None:
        return None

    first = await commander.propose("Fahre meinen PC herunter", "de", "a")
    second = await commander.answer(first.pending, True, "de", "a", deliver, {})
    assert second.pending and not ran
    await commander.answer(second.pending, True, "de", "a", deliver, {})
    assert ran == ["pc"]
    app.db.close()


async def test_state_questions_are_answered_directly(tmp_path: Path) -> None:
    app = smart_app(tmp_path)
    outcome = await app.addons.commander.propose("Wie lange geht noch die Spülmaschine?", "de", "a")
    assert outcome.pending is None and "42 min" in outcome.text
    app.db.close()


def test_ai_plans_are_checked(tmp_path: Path) -> None:
    app = smart_app(tmp_path)
    commander = Commander(app)
    assert commander._checked({"action": "shell", "command": "rm -rf /"}, "x") is None
    assert commander._checked({"action": "mode", "mode": "ROOT"}, "x") is None
    assert commander._checked({"action": "switch", "device": "Toaster"}, "x") is None
    assert commander._checked({"action": "remote", "name": "unbekannt"}, "x") is None
    plan = commander._checked({"action": "switch", "device": "schlafzimmer", "on": True}, "x")
    assert plan == Proposal("switch", {"device": "schlaf", "on": True})
    app.db.close()


async def test_viewers_cannot_trigger_actions(tmp_path: Path, monkeypatch: Any) -> None:
    app = smart_app(tmp_path)
    await app.messenger.handle(msg("/frage Licht Schlafzimmer aus", VIEWER))
    assert not app.providers["telegram"].last().buttons or "proposal" not in str(
        app.providers["telegram"].last().buttons
    )
    await app.messenger.handle(msg("/frage Licht Schlafzimmer aus", ADMIN))
    reply = app.providers["telegram"].last()
    assert reply.buttons[0][0].command.startswith("proposal yes")
    await app.messenger.handle(msg(reply.buttons[0][0].command, VIEWER, from_button=True))
    assert app.addons.smarthome.switched == []
    await app.messenger.handle(msg(reply.buttons[0][0].command, ADMIN, from_button=True))
    assert app.addons.smarthome.switched == [("schlaf", False)]
    app.db.close()


def test_next_time_is_in_the_future() -> None:
    from pi_guard.core.timeutil import utcnow

    tz = ZoneInfo("Europe/Berlin")
    start = next_time("03:00", tz)
    assert start > utcnow()
    assert next_time("04:00", tz, after=start) > start
    with pytest.raises(ValueError):
        next_time("25:00", tz)


async def test_planned_recording_is_kept_and_sent(tmp_path: Path) -> None:
    from pi_guard.core.timeutil import utcnow

    app = make_app(tmp_path)
    jobs = Jobs(app)
    jobs.add("record", utcnow(), {"minutes": 60})
    assert Jobs(app).list()[0]["args"]["minutes"] == 60  # survives a restart
    assert jobs.delete(jobs.list()[0]["id"]) and jobs.list() == []
    app.db.close()


async def test_pictures_go_out_only_when_allowed(db: Any, tmp_path: Path, monkeypatch: Any) -> None:
    from .test_features import Api, make

    picture = tmp_path / "p.jpg"
    picture.write_bytes(b"\xff\xd8x")
    api = Api(monkeypatch)
    from pi_guard.addons.assistant import AssistantError

    with pytest.raises(AssistantError, match="no_vision"):
        await make(db, tmp_path).describe(picture, "Was siehst du?", "de")
    assert not api.requests
    await make(db, tmp_path, allow_vision=True).describe(picture, "Was siehst du?", "de")
    body = json.loads(api.requests[0].content)
    assert body["messages"][1]["content"][0]["type"] == "image_url"
    await make(db, tmp_path, provider="anthropic", allow_vision=True).describe(picture, "x", "de")
    assert json.loads(api.requests[1].content)["messages"][0]["content"][0]["type"] == "image"
