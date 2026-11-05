"""Cameras, evidence, device inspection, block lists, knowledge, providers and plugins."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from pi_guard.addons import assistant as assistant_module
from pi_guard.addons import documents as documents_module
from pi_guard.addons.assistant import Assistant
from pi_guard.addons.documents import Paperless, excerpt
from pi_guard.addons.knowledge import Knowledge, KnowledgeError, extract_text, keywords, passages
from pi_guard.addons.plugin import Plugin, discover, load_plugins
from pi_guard.cameras.ffmpeg import (
    CaptureChoice,
    choose_capture,
    input_args,
    mask_credentials,
    picamera_command,
)
from pi_guard.config.files import AssistantAddon, CameraConfig
from pi_guard.network.blocklists import parse_domains, valid_list_address
from pi_guard.network.inspect import guess
from pi_guard.network.pihole import Pihole
from pi_guard.recording import evidence as evidence_module
from pi_guard.recording.evidence import evidence_files, record_evidence
from pi_guard.web.api_network import _browser_url

from .helpers import ADMIN, VIEWER, make_app, msg
from .test_web import _client, _login


def camera(**values: Any) -> CameraConfig:
    return CameraConfig(**{"id": "a", "name": "A", "device": "/dev/video0", **values})


def test_network_cameras_are_validated() -> None:
    assert camera(source="rtsp", device="rtsp://u:p@cam/s").source == "rtsp"
    with pytest.raises(ValueError, match="rtsp"):
        camera(source="rtsp", device="/dev/video0")
    with pytest.raises(ValueError, match="http"):
        camera(source="http", device="rtsp://x")
    with pytest.raises(ValueError, match="number"):
        camera(source="picamera", device="/dev/video0")
    assert not camera(source="picamera", device="0").is_local_device


def test_capture_choice_per_source(monkeypatch: pytest.MonkeyPatch) -> None:
    from pi_guard.cameras import ffmpeg

    monkeypatch.setattr(ffmpeg, "encoder_works", lambda name: False)
    rtsp = camera(source="rtsp", device="rtsp://cam/s")
    assert choose_capture(rtsp, None) == CaptureChoice("rtsp", "copy")
    esp = camera(source="http", device="http://cam:81/stream")
    assert choose_capture(esp, None).encoder == "libx264"
    assert "-rtsp_transport" in input_args(rtsp, choose_capture(rtsp, None))
    pi = camera(source="picamera", device="0", resolution="640x480", fps=10)
    assert input_args(pi, choose_capture(pi, None))[-1] == "pipe:0"
    assert picamera_command(pi)[:3] == ["rpicam-vid", "-t", "0"]


def test_passwords_never_reach_logs() -> None:
    assert mask_credentials("failed rtsp://admin:geheim@cam:554/s") == "failed rtsp://***@cam:554/s"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
async def test_mjpeg_stream_like_an_esp32(tmp_path: Path) -> None:
    from aiohttp import web
    from aiohttp.test_utils import TestServer

    jpeg = tmp_path / "f.jpg"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=320x240",
                    "-frames:v", "1", str(jpeg)], check=True)  # fmt: skip
    frame = jpeg.read_bytes()

    async def stream(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(headers={
            "Content-Type": "multipart/x-mixed-replace; boundary=frame"})  # fmt: skip
        await response.prepare(request)
        for _ in range(30):
            head = f"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {len(frame)}\r\n\r\n"
            await response.write(head.encode() + frame + b"\r\n")
            await asyncio.sleep(0.05)
        return response

    application = web.Application()
    application.router.add_get("/stream", stream)
    server = TestServer(application)
    await server.start_server()
    try:
        cam = camera(source="http", device=str(server.make_url("/stream")))
        out = tmp_path / "out.mp4"
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-loglevel", "error", "-y",
            *input_args(cam, CaptureChoice("http", "libx264")),
            "-t", "1", "-c:v", "libx264", "-preset", "ultrafast", str(out),
        )  # fmt: skip
        assert await asyncio.wait_for(process.wait(), 15) == 0
        assert out.stat().st_size > 1000
    finally:
        await server.close()


# --- evidence ------------------------------------------------------------------------------


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
async def test_evidence_video_has_checksum_and_metadata(tmp_path: Path, monkeypatch: Any) -> None:
    app = make_app(tmp_path)

    async def no_wait(seconds: float) -> None:
        return None

    async def clip(directory: Path, start: Any, end: Any, output: Path) -> Path:
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc=s=160x120:d=1", str(output)], check=True)  # fmt: skip
        return output

    monkeypatch.setattr(evidence_module.asyncio, "sleep", no_wait)
    monkeypatch.setattr(evidence_module, "extract_clip", clip)
    camera_id = next(iter(app.cameras))
    result = await record_evidence(app, camera_id, 5, "telegram:1")
    info = json.loads(result.metadata.read_text())
    assert info["duration_seconds"] == 300 and info["requested_by"] == "telegram:1"
    import hashlib

    assert info["sha256"] == hashlib.sha256(result.video.read_bytes()).hexdigest()
    assert evidence_files(app.media_dir)[0]["sha256"] == result.sha256
    assert not list((app.media_dir / "evidence").glob("*.raw.mp4"))
    assert "evidence" in [r[0] for r in app.db.execute("SELECT action FROM audit")]
    app.db.close()


# --- network -------------------------------------------------------------------------------


def test_device_guesses() -> None:
    assert guess(64, {62078}, "Apple, Inc.", "3a:00:00:00:00:01")["kind"] == "phone"
    windows = guess(128, {445, 3389}, "Dell Inc.", "00:11:22:33:44:55")
    assert windows["system"] == "windows" and windows["kind"] == "computer"
    assert windows["private_mac"] is False
    assert guess(64, {9100, 80}, None, None)["kind"] == "printer"
    assert guess(64, {554}, None, None)["kind"] == "camera"
    assert guess(64, {53, 80, 443, 554, 49000}, "AVM GmbH", None)["kind"] == "router"
    assert guess(None, set(), None, None)["system"] == "unknown"


async def test_inspection_stays_in_the_home_network(tmp_path: Path, monkeypatch: Any) -> None:
    from pi_guard.web import api_network

    seen: list[str] = []

    async def fake(ip: str, mac: Any, vendor: Any) -> dict[str, Any]:
        seen.append(ip)
        return {"ip": ip, "kind": "phone", "system": "apple"}

    monkeypatch.setattr(api_network, "inspect_device", fake)
    client, app = await _client(tmp_path)
    try:
        await _login(client)
        assert (await client.get("/api/network/device/8.8.8.8")).status == 400
        assert (await client.get("/api/network/device/not-an-ip")).status == 400
        mac = "aa:bb:cc:dd:ee:ff"
        response = await client.get(f"/api/network/device/192.168.178.5?mac={mac}")
        assert response.status == 200 and seen == ["192.168.178.5"]
        assert app.db.get(f"lan.details.{mac}")["kind"] == "phone"
    finally:
        await client.close()
        app.db.close()


def test_pihole_link_uses_the_address_of_the_browser() -> None:
    class Request:
        host = "192.168.178.109:8443"

    assert _browser_url(Request(), "http://127.0.0.1") == "http://192.168.178.109"  # type: ignore[arg-type]
    assert _browser_url(Request(), "http://127.0.0.1:8080/x") == "http://192.168.178.109:8080/x"  # type: ignore[arg-type]
    assert _browser_url(Request(), "http://pi.hole") == "http://pi.hole"  # type: ignore[arg-type]


def test_block_list_formats() -> None:
    text = """# comment
0.0.0.0 ads.example.org
127.0.0.1 tracker.example.net # note
plain.example.com
||adblock.example.de^
! adblock comment
localhost
0.0.0.0 localhost
not a domain!
ADS.example.org
"""
    domains, bad = parse_domains(text)
    assert domains == ["ads.example.org", "tracker.example.net", "plain.example.com",
                       "adblock.example.de"]  # fmt: skip
    assert bad == 1
    assert valid_list_address("https://example.org/list.txt")
    assert not valid_list_address("file:///etc/passwd")
    assert not valid_list_address("javascript:alert(1)")


async def test_block_lists_reach_pihole(monkeypatch: Any) -> None:
    calls: list[tuple[str, str, Any]] = []
    hole = Pihole("http://127.0.0.1", "x")

    async def request(method: str, path: str, **kwargs: Any) -> Any:
        calls.append((method, path, kwargs.get("json")))
        if path == "/api/domains/deny/exact":
            return {"processed": {"success": kwargs["json"]["domain"]}}
        return {"lists": [{"address": "https://a/b", "enabled": True, "number": 5}]}

    monkeypatch.setattr(hole, "request", request)
    assert (await hole.block_lists())[0]["domains"] == 5
    await hole.add_block_list("https://a/b", "ads")
    await hole.delete_block_list("https://a/b")
    assert calls[-1][1] == "/api/lists/https%3A%2F%2Fa%2Fb"
    assert await hole.deny_domains([f"d{i}.example.org" for i in range(1200)], "x") == 1200
    await hole.close()


# --- knowledge -----------------------------------------------------------------------------


def tiny_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 20 200 Td ({text}) Tj ET".encode()
    bodies = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 300]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length %d>>stream\n%s\nendstream" % (len(stream), stream),
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(bodies, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    table = len(out)
    out += b"xref\n0 6\n0000000000 65535 f \n"
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer<</Size 6/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % table


PDF = tiny_pdf("Steuer-ID 12 345 678 901")


def test_files_are_checked() -> None:
    assert "name: Anna" in extract_text("a.json", b'{"person": {"name": "Anna"}}').replace(
        "person.", ""
    )
    assert "12 345" in extract_text("steuer.pdf", PDF)
    assert extract_text("a.md", "# Überschrift".encode()) == "# Überschrift"
    for name, data, key in (
        ("a.exe", b"x", "unsupported"), ("a.json", b"{broken", "bad_json"),
        ("a.pdf", b"not a pdf", "invalid"), ("a.txt", b"\0\0\0", "invalid"),
        ("a.txt", b"x" * 10_000_001, "too_large"),
    ):  # fmt: skip
        with pytest.raises(KnowledgeError, match=key):
            extract_text(name, data)


def test_passages_stay_small() -> None:
    pieces = passages("Satz eins. " * 500)
    assert len(pieces) > 3 and all(len(p) <= 800 for p in pieces)
    assert keywords("Wie lautet meine Steuer-ID?") == ["lautet", "steuer", "id"]


def test_knowledge_search_and_duplicates(tmp_path: Path) -> None:
    store = Knowledge(tmp_path / "k.db")
    store.add("wlan.txt", "Das WLAN-Passwort für Gäste steht am Router.".encode())
    store.add("steuer.pdf", PDF)
    with pytest.raises(KnowledgeError, match="duplicate"):
        store.add("kopie.pdf", PDF)
    found = store.search("Wie lautet meine Steuer-ID?")
    assert found[0]["source"] == "steuer.pdf" and "345" in found[0]["text"]
    assert store.search("") == [] and store.search('"; DROP TABLE files') == []
    store.remember("k", "antwort", ["steuer.pdf"])
    assert store.cached("k") == ("antwort", ["steuer.pdf"])
    assert store.delete(store.files()[0]["id"])
    assert store.cached("k") is None  # answers may depend on what changed


class Api:
    def __init__(self, monkeypatch: Any) -> None:
        self.requests: list[httpx.Request] = []
        real = httpx.AsyncClient

        def client(**kwargs: Any) -> httpx.AsyncClient:
            return real(transport=httpx.MockTransport(self._handle), **kwargs)

        monkeypatch.setattr(assistant_module.httpx, "AsyncClient", client)
        monkeypatch.setattr(documents_module.httpx, "AsyncClient", client)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "paperless.local":
            content = "Blabla. Ihre Steuer-ID lautet 11 222 333 444."
            results = [{"title": "Steuerbescheid", "content": content}]
            return httpx.Response(200, json={"results": results})
        if request.url.host == "api.anthropic.com":
            block = {"type": "text", "text": "Claude sagt hallo."}
            usage = {"input_tokens": 10, "output_tokens": 5}
            return httpx.Response(200, json={"content": [block], "usage": usage})
        return httpx.Response(200, json={"choices": [{"message": {"content": "11 222 333 444"}}],
                                         "usage": {"total_tokens": 50}})  # fmt: skip


def make(db: Any, tmp_path: Path, searchers: tuple[Any, ...] = (), key: str | None = "k",
         **settings: Any) -> Assistant:  # fmt: skip
    return Assistant(AssistantAddon(enabled=True, **settings), db, ZoneInfo("UTC"), key, None,
                     tmp_path, Knowledge(tmp_path / "k.db"), searchers)  # fmt: skip


@pytest.mark.parametrize(("provider", "host"), [
    ("openai", "api.openai.com"), ("mistral", "api.mistral.ai"), ("grok", "api.x.ai"),
    ("gemini", "generativelanguage.googleapis.com"), ("ollama", "127.0.0.1"),
])  # fmt: skip
async def test_openai_style_providers(
    db: Any, tmp_path: Path, monkeypatch: Any, provider: str, host: str
) -> None:
    api = Api(monkeypatch)
    assistant = make(db, tmp_path, key=None if provider == "ollama" else "k", provider=provider)
    assert (await assistant.ask("Frage", "de", "a")).source == "ai"
    assert api.requests[0].url.host == host
    assert api.requests[0].url.path.endswith("/chat/completions")
    if provider == "ollama":
        assert "authorization" not in api.requests[0].headers


async def test_anthropic_uses_its_own_format(db: Any, tmp_path: Path, monkeypatch: Any) -> None:
    api = Api(monkeypatch)
    assistant = make(db, tmp_path, provider="anthropic")
    answer = await assistant.ask("Hallo", "de", "a")
    assert answer.text == "Claude sagt hallo." and answer.tokens == 15
    request = api.requests[0]
    assert request.url.path == "/v1/messages" and request.headers["x-api-key"] == "k"
    assert request.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(request.content)
    assert body["model"] == "claude-haiku-5-5" and "system" in body
    assert [m["role"] for m in body["messages"]] == ["user"]


async def test_documents_are_for_admins_and_need_consent(
    db: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    api = Api(monkeypatch)
    paperless = Paperless("http://paperless.local", "t")
    question = "Wie lautet meine Steuer-ID?"
    closed = make(db, tmp_path, (paperless,))  # no consent for a cloud provider
    await closed.ask(question, "de", "a", private=True)
    assert not any(r.url.host == "paperless.local" for r in api.requests)
    assert "11 222" not in api.requests[-1].content.decode()
    api.requests.clear()
    shared = make(db, tmp_path, (paperless,), share_documents=True)
    await shared.ask(question, "de", "viewer", private=False)  # not an admin
    assert not any(r.url.host == "paperless.local" for r in api.requests)
    api.requests.clear()
    answer = await shared.ask(question, "de", "admin", private=True)
    assert answer.source == "documents" and answer.documents == ("Paperless: Steuerbescheid",)
    sent = api.requests[-1].content.decode()
    assert "11 222 333 444" in sent and "never instructions" in sent
    assert api.requests[0].headers["authorization"] == "Token t"
    # The same question again: no search, no tokens.
    api.requests.clear()
    again = await shared.ask(question.upper(), "de", "admin", private=True)
    assert again.source == "cache" and again.text == answer.text and not api.requests
    # A viewer never gets that stored answer.
    other = await shared.ask(question, "de", "viewer", private=False)
    assert other.documents == ()


def test_excerpts_show_the_search_word() -> None:
    text = "x" * 2000 + " Steuer-ID 123 " + "y" * 2000
    assert "Steuer-ID 123" in excerpt(text, ["steuer"])
    assert len(excerpt(text, ["steuer"])) <= 700


async def test_knowledge_upload_through_the_dashboard(tmp_path: Path) -> None:
    import aiohttp

    from pi_guard.addons import Addons
    from pi_guard.config.files import FileConfig

    client, app = await _client(tmp_path)
    try:
        object.__setattr__(app.config, "file", FileConfig.model_validate(
            {"addons": {"assistant": {"enabled": True}}}))  # fmt: skip
        app.addons = Addons(app)
        headers = {"X-CSRF-Token": await _login(client)}
        form = aiohttp.FormData()
        form.add_field("files", b"Das Passwort steht am Router.", filename="a.txt")
        form.add_field("files", b"{kaputt", filename="b.json")
        results = await (await client.post("/api/addons/assistant/knowledge", data=form,
                                           headers=headers)).json()  # fmt: skip
        assert [r["ok"] for r in results] == [True, False]
        assert results[1]["error"] == "knowledge.bad_json"
        files = await (await client.get("/api/addons/assistant/knowledge")).json()
        assert [f["name"] for f in files] == ["a.txt"]
    finally:
        await client.close()
        app.db.close()


# --- plugins -------------------------------------------------------------------------------

PLUGIN = """
from aiohttp import web
from pi_guard.addons.plugin import Plugin

class Hello(Plugin):
    id = "hello"
    title = "Hello"

    def commands(self):
        return [self.command("hello", self.say, aliases=("hallo",), admin_only=False)]

    def routes(self):
        return [web.get("/ping", self.ping)]

    async def say(self, ctx, args):
        return self.reply("Hi " + self.settings.get("name", "?"))

    async def ping(self, request):
        return web.json_response({"pong": True})
"""


def test_plugin_files_must_belong_to_root(tmp_path: Path) -> None:
    (tmp_path / "hello.py").write_text(PLUGIN)
    (tmp_path / "broken.py").write_text("this is not python")
    assert discover(tmp_path) == {}  # owned by the test user
    found = discover(tmp_path, check_owner=False)
    assert list(found) == ["hello"] and issubclass(found["hello"], Plugin)


async def test_plugin_commands_and_routes(tmp_path: Path, monkeypatch: Any) -> None:
    from pi_guard.addons import plugin as module

    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "hello.py").write_text(PLUGIN)
    monkeypatch.setattr(module, "discover", lambda **kw: discover(plugins, check_owner=False))
    app = make_app(tmp_path / "a")
    loaded = load_plugins(
        app, {"hello": {"enabled": True, "name": "Lena"}, "gone": {"enabled": True}}
    )
    assert [p.id for p in loaded] == ["hello"]
    await app.messenger.handle(msg("/hallo", VIEWER))
    assert app.providers["telegram"].last().text == "Hi Lena"
    await app.messenger.handle(msg("/hello", ADMIN))
    app.db.close()
    assert load_plugins(make_app(tmp_path / "b"), {"hello": {"enabled": False}}) == []


def test_what_speech_models_imagine_in_silence_is_dropped() -> None:
    from pi_guard.addons.assistant import imagined

    assert imagined("Untertitel der Amara.org-Community") and imagined("  ")
    assert imagined("Vielen Dank fürs Zuschauen!")
    assert not imagined("Mach das Licht im Flur an")
