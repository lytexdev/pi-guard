from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
import yaml

from pi_guard.addons import assistant as assistant_module
from pi_guard.addons.analytics import NetworkAnalytics, base_domain, group_domains
from pi_guard.addons.assistant import Assistant, AssistantError
from pi_guard.addons.heartbeat import HeartbeatWatcher
from pi_guard.addons.remote import ConfirmationError, RemoteActions
from pi_guard.addons.smarthome import Live, SmartHome, SmartHomeError, from_discovery, read_value
from pi_guard.config import OVERRIDE_YAML
from pi_guard.config.files import (
    AssistantAddon,
    FileConfig,
    HeartbeatTarget,
    RemoteAction,
    SmartDevice,
)
from pi_guard.core.events import EventBus, EventType
from pi_guard.messaging.base import Reply
from pi_guard.messaging.whatsapp.provider import (
    WhatsAppProvider,
    parse_webhook,
    valid_signature,
)
from pi_guard.web.api_addons import classify

from .helpers import ADMIN, make_app, msg
from .test_web import _client, _login

# --- heartbeat -----------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def test_heartbeat_reports_each_outage_once(db: Any) -> None:
    port = free_port()
    target = HeartbeatTarget(id="nas", name="NAS", kind="tcp", address=f"127.0.0.1:{port}",
                             timeout_seconds=1, failures_before_alert=2)  # fmt: skip
    bus = EventBus()
    events = bus.subscribe("t")
    watcher = HeartbeatWatcher([target], bus, db)
    await watcher.check(target)
    assert events.empty()  # a single failure is no outage
    await watcher.check(target)
    await watcher.check(target)
    assert events.qsize() == 1
    down = events.get_nowait()
    assert down.type is EventType.SERVICE_DOWN and down.data["name"] == "NAS"
    assert str(port) not in json.dumps(down.data)  # addresses can contain tokens

    # After a restart a known outage is not reported again, its end is.
    restarted = HeartbeatWatcher([target], bus, db)
    await restarted.check(target)
    await restarted.check(target)
    assert events.empty()
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", port)
    await restarted.check(target)
    server.close()
    assert events.get_nowait().type is EventType.SERVICE_UP
    assert restarted.overview()[0]["up"] is True
    assert db.get("heartbeat.down") == []


async def test_heartbeat_http_and_ping(db: Any, tmp_path: Path) -> None:
    from aiohttp import web
    from aiohttp.test_utils import TestServer

    async def page(request: web.Request) -> web.Response:
        return web.Response(status=int(request.query.get("s", "200")), text="hello login")

    application = web.Application()
    application.router.add_get("/", page)
    server = TestServer(application)
    await server.start_server()
    try:
        base = str(server.make_url("/"))
        make = lambda **kw: HeartbeatTarget(id="a", name="A", address=base, **kw)  # noqa: E731
        bus = EventBus()

        async def result(target: HeartbeatTarget) -> Any:
            watcher = HeartbeatWatcher([target], bus, db)
            await watcher.check(target)
            return watcher.state["a"]

        assert (await result(make())).failures == 0
        assert (await result(make(expected_text="login"))).failures == 0
        assert (await result(make(expected_text="missing"))).failures == 1
        broken = HeartbeatTarget(id="a", name="A", address=base + "?s=503")
        assert (await result(broken)).reason == "HTTP 503"
        locked = HeartbeatTarget(id="a", name="A", address=base + "?s=401")
        assert (await result(locked)).failures == 0  # refuses us, but it is alive
    finally:
        await server.close()
    if shutil.which("ping"):
        ping = HeartbeatTarget(id="a", name="A", kind="ping", address="127.0.0.1")
        assert (await result(ping)).failures == 0


def test_heartbeat_addresses_are_validated() -> None:
    with pytest.raises(ValueError, match="http"):
        HeartbeatTarget(id="a", name="A", address="ftp://x")
    with pytest.raises(ValueError, match="host:port"):
        HeartbeatTarget(id="a", name="A", kind="tcp", address="nas")
    with pytest.raises(ValueError, match="host name"):
        HeartbeatTarget(id="a", name="A", kind="ping", address="-f; reboot")


# --- analytics -----------------------------------------------------------------------------


def test_domains_are_grouped_by_service() -> None:
    assert base_domain("mail.proton.me") == "proton.me"
    assert base_domain("www.example.co.uk") == "example.co.uk"
    rows = [
        {"domain": "mail.proton.me", "count": 5},
        {"domain": "calendar.proton.me", "count": 3},
        {"domain": "1.0.168.192.in-addr.arpa", "count": 99},
        {"domain": "a.example.org", "count": 1},
    ]  # fmt: skip
    assert group_domains(rows, 5) == [{"domain": "proton.me", "count": 8},
                                      {"domain": "example.org", "count": 1}]  # fmt: skip


async def test_device_analysis_separates_blocked_domains() -> None:
    class Hole:
        async def request(self, method: str, path: str, **kwargs: Any) -> Any:
            assert kwargs["params"]["client_ip"] == "192.168.178.40"
            return {"queries": [
                {"domain": "www.heise.de", "status": "FORWARDED", "time": 5},
                {"domain": "cdn.heise.de", "status": "CACHE", "time": 7},
                {"domain": "ads.tracker.com", "status": "GRAVITY", "time": 6},
            ]}  # fmt: skip

    analytics = NetworkAnalytics(Hole())  # type: ignore[arg-type]
    result = await analytics.device("192.168.178.40")
    assert result["top_domains"] == [{"domain": "heise.de", "count": 2}]
    assert result["top_blocked"] == [{"domain": "tracker.com", "count": 1}]
    with pytest.raises(ValueError):
        await analytics.device("192.168.178.40&length=99999")


# --- smart home ----------------------------------------------------------------------------


def test_discovery_messages() -> None:
    plug = from_discovery("homeassistant/switch/plug1/config", json.dumps({
        "name": "Steckdose", "uniq_id": "Plug-1", "~": "tasmota/plug1", "stat_t": "~/stat",
        "cmd_t": "~/cmnd", "pl_on": "1", "pl_off": "0", "dev": {"name": "Flur"},
    }).encode())  # fmt: skip
    assert plug is not None
    assert (plug.state_topic, plug.command_topic) == ("tasmota/plug1/stat", "tasmota/plug1/cmnd")
    assert plug.name == "Flur Steckdose" and plug.payload_on == "1"
    sensor = from_discovery("homeassistant/sensor/z2m/temp/config", json.dumps({
        "name": "Temperatur", "unique_id": "t1", "state_topic": "zigbee2mqtt/bad",
        "value_template": "{{ value_json.temperature }}", "unit_of_measurement": "°C",
        "command_topic": "ignored",
    }).encode())  # fmt: skip
    assert sensor is not None and sensor.value_key == "temperature" and sensor.unit == "°C"
    assert sensor.command_topic is None
    light = from_discovery("homeassistant/light/z2m/l/config", json.dumps({
        "name": "Licht", "unique_id": "l1", "schema": "json", "command_topic": "z/l/set",
        "state_topic": "z/l"}).encode())  # fmt: skip
    assert light is not None and light.payload_on == '{"state":"ON"}' and light.value_key == "state"
    for topic, payload in (
        ("homeassistant/camera/x/config", b'{"name":"a","state_topic":"b"}'),
        ("homeassistant/switch/x/config", b""),
        ("homeassistant/switch/x/config", b"not json"),
        ("homeassistant/switch/x/config", b'{"name":"no topics"}'),
        ("homeassistant/switch/x/config", b'{"command_topic":"a/#"}'),
    ):
        assert from_discovery(topic, payload) is None


def test_values_are_read_and_shortened() -> None:
    plain = SmartDevice(id="a", name="A", state_topic="t")
    assert read_value(plain, b"  ON ") == "ON"
    assert len(read_value(plain, b"x" * 500) or "") == 40
    keyed = SmartDevice(id="a", name="A", state_topic="t", value_key="state")
    assert read_value(keyed, b'{"state":"OFF"}') == "OFF"
    assert read_value(keyed, b"no json") is None
    assert read_value(keyed, b'{"other":1}') is None


async def test_switching_only_reaches_known_devices(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    home = object.__new__(SmartHome)
    lamp = SmartDevice(id="lampe", name="Stehlampe", command_topic="home/lamp/set",
                       state_topic="home/lamp")  # fmt: skip
    sensor = SmartDevice(id="temp", name="Temperatur", kind="sensor", state_topic="home/t")
    home.live = {"lampe": Live(lamp), "temp": Live(sensor)}

    class Client:
        sent: list[tuple[str, str]] = []  # noqa: RUF012

        async def publish(self, topic: str, payload: str, qos: int = 0) -> None:
            self.sent.append((topic, payload))

    home._client = None
    with pytest.raises(SmartHomeError, match="offline"):
        await home.switch("lampe", True)
    home._client = Client()
    await home.switch("lampe", True)
    home.live["lampe"].value = "ON"
    await home.switch("lampe", None)  # toggle
    assert Client.sent == [("home/lamp/set", "ON"), ("home/lamp/set", "OFF")]
    for wrong in ("temp", "nope"):
        with pytest.raises(SmartHomeError, match="not_switchable"):
            await home.switch(wrong, True)
    assert home.find("steh") is home.live["lampe"]
    app.db.close()


# --- remote actions ------------------------------------------------------------------------

ACTION = RemoteAction(id="pc", name="PC aus", host="192.168.178.20", user="me", command="poweroff")


def actions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RemoteActions:
    from pi_guard.addons import remote

    monkeypatch.setattr(remote, "MIN_SECONDS_BETWEEN_STEPS", 0)
    return RemoteActions([ACTION], tmp_path)


def test_two_confirmations_by_the_same_person(tmp_path: Path, monkeypatch: Any) -> None:
    remote = actions(tmp_path, monkeypatch)
    first = remote.begin("pc", "telegram:1")
    action, second = remote.confirm(first, "telegram:1")
    assert action.id == "pc" and second and second != first
    with pytest.raises(ConfirmationError):  # tokens work once
        remote.confirm(first, "telegram:1")
    with pytest.raises(ConfirmationError):  # and for one person
        remote.confirm(second, "telegram:2")
    token = remote.begin("pc", "telegram:1")
    remote.cancel(token)
    with pytest.raises(ConfirmationError):
        remote.confirm(token, "telegram:1")
    with pytest.raises(ConfirmationError, match="unknown"):
        remote.begin("other", "telegram:1")


def test_a_double_tap_does_not_confirm(tmp_path: Path) -> None:
    remote = RemoteActions([ACTION], tmp_path)
    with pytest.raises(ConfirmationError, match="too_fast"):
        remote.confirm(remote.begin("pc", "a"), "a")


def test_confirmations_expire(tmp_path: Path, monkeypatch: Any) -> None:
    from pi_guard.addons import remote as module

    remote = actions(tmp_path, monkeypatch)
    token = remote.begin("pc", "a")
    monkeypatch.setattr(module, "CONFIRM_SECONDS", -1)
    with pytest.raises(ConfirmationError, match="expired"):
        remote.confirm(token, "a")


def test_ssh_command_line_cannot_be_turned_into_options(tmp_path: Path) -> None:
    line = RemoteActions([ACTION], tmp_path).command_line(ACTION)
    assert line[line.index("--") + 1 :] == ["192.168.178.20", "poweroff"]
    assert "StrictHostKeyChecking=accept-new" in line and "BatchMode=yes" in line
    with pytest.raises(ValueError):
        RemoteAction(id="a", name="A", host="-oProxyCommand=x", user="me", command="c")
    with pytest.raises(ValueError):
        RemoteAction(id="a", name="A", host="h", user="-oX", command="c")


@pytest.mark.skipif(not shutil.which("sshd") or not shutil.which("ssh-keygen"), reason="needs sshd")
async def test_action_runs_over_real_ssh(tmp_path: Path) -> None:
    import getpass

    port = free_port()
    remote_dir = tmp_path / "server"
    remote_dir.mkdir(mode=0o700)
    host_key = remote_dir / "host"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(host_key)], check=True)
    marker = tmp_path / "ran"
    action = RemoteAction(id="a", name="A", host="127.0.0.1", port=port, user=getpass.getuser(),
                          command=f"touch {marker} && echo done")  # fmt: skip
    remote = RemoteActions([action], tmp_path)
    keys = remote_dir / "authorized_keys"
    keys.write_text(await remote.public_key() + "\n")
    keys.chmod(0o600)
    config = remote_dir / "sshd_config"
    config.write_text(
        f"Port {port}\nListenAddress 127.0.0.1\nHostKey {host_key}\nAuthorizedKeysFile {keys}\n"
        f"PidFile {remote_dir / 'pid'}\nStrictModes no\nUsePAM no\n"
    )
    sshd = shutil.which("sshd")
    assert sshd is not None
    process = subprocess.Popen(  # noqa: ASYNC220
        [sshd, "-D", "-e", "-f", str(config)], stderr=subprocess.DEVNULL
    )
    try:
        for _ in range(50):
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            await asyncio.sleep(0.1)
        result = await remote.run(action)
        assert result.ok and "done" in result.output and marker.exists()
        # A computer that suddenly has another key is refused.
        other = remote_dir / "other"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(other)], check=True
        )
        key = other.with_suffix(".pub").read_text().split()[:2]
        (tmp_path / "remote" / "known_hosts").write_text(f"[127.0.0.1]:{port} {' '.join(key)}\n")
        marker.unlink()
        assert not (await remote.run(action)).ok
        assert not marker.exists()
    finally:
        process.terminate()
        process.wait()


# --- assistant -----------------------------------------------------------------------------


class Api:
    """Stands in for the provider and Open-Meteo; records what was sent."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200
        real = httpx.AsyncClient

        def client(**kwargs: Any) -> httpx.AsyncClient:
            return real(transport=httpx.MockTransport(self._handle), **kwargs)

        monkeypatch.setattr(assistant_module.httpx, "AsyncClient", client)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if "geocoding" in request.url.host:
            return httpx.Response(200, json={"results": [
                {"latitude": 52.5, "longitude": 13.4, "name": "Berlin"}]})  # fmt: skip
        if "open-meteo" in request.url.host:
            return httpx.Response(200, json={
                "current": {"temperature_2m": 11.4},
                "daily": {"temperature_2m_min": [6.2, 4.0], "temperature_2m_max": [13.0, 9.0],
                          "precipitation_probability_max": [20, 80], "weather_code": [3, 61]},
            })  # fmt: skip
        if "deepl" in request.url.host:
            return httpx.Response(200, json={"translations": [{"text": "Good morning"}]})
        if self.status != 200:
            return httpx.Response(self.status, json={})
        content = {"choices": [{"message": {"content": "Paris."}}], "usage": {"total_tokens": 40}}
        return httpx.Response(200, json=content)


def make_assistant(db: Any, tmp_path: Path, key: str | None = "k", **settings: Any) -> Assistant:
    config = AssistantAddon(enabled=True, location="Berlin", **settings)
    deepl = settings.pop("deepl", None)
    return Assistant(config, db, ZoneInfo("Europe/Berlin"), key, deepl, tmp_path)


async def test_repeated_questions_cost_nothing(db: Any, tmp_path: Path, monkeypatch: Any) -> None:
    api = Api(monkeypatch)
    assistant = make_assistant(db, tmp_path, history_turns=0)
    first = await assistant.ask("Hauptstadt von Frankreich?", "de", "a")
    second = await assistant.ask("  hauptstadt   von frankreich? ", "de", "b")
    assert (first.source, first.tokens) == ("ai", 40)
    assert (second.source, second.tokens) == ("cache", 0)
    assert len(api.requests) == 1 and assistant.tokens_today == 40


async def test_prompt_stays_small_and_private(db: Any, tmp_path: Path, monkeypatch: Any) -> None:
    api = Api(monkeypatch)
    assistant = make_assistant(db, tmp_path, history_turns=1, max_answer_tokens=120)
    for number in range(3):
        await assistant.ask(f"Frage {number}", "de", "chat")
    body = json.loads(api.requests[-1].content)
    assert body["max_tokens"] == 120
    assert [m["role"] for m in body["messages"]] == ["system", "user", "assistant", "user"]
    assert body["model"] == "mistral-small-latest"
    assert "Berlin" not in api.requests[-1].content.decode()
    assert api.requests[-1].headers["authorization"] == "Bearer k"


async def test_daily_limit_stops_spending(db: Any, tmp_path: Path, monkeypatch: Any) -> None:
    api = Api(monkeypatch)
    assistant = make_assistant(db, tmp_path, daily_token_limit=1000)
    db.set(assistant._usage_key(), 1000)
    with pytest.raises(AssistantError, match="limit_reached"):
        await assistant.ask("Frage", "de", "a")
    assert not api.requests
    assert "Berlin" in (await assistant.ask("Wie wird das Wetter?", "de", "a")).text  # free


async def test_weather_and_time_use_no_tokens(db: Any, tmp_path: Path, monkeypatch: Any) -> None:
    Api(monkeypatch)
    assistant = make_assistant(db, tmp_path, key=None)
    today = await assistant.ask("Wie wird das Wetter?", "de", "a")
    assert today.source == "weather" and today.tokens == 0
    assert "11 °C" in today.text and "bewölkt" in today.text
    tomorrow = await assistant.ask("Wetter morgen in Berlin?", "de", "a")
    assert "Regen" in tomorrow.text and "80 %" in tomorrow.text
    assert (await assistant.ask("Wie spät ist es?", "de", "a")).source == "time"
    with pytest.raises(AssistantError, match="no_key"):
        await assistant.ask("Was ist Photosynthese?", "de", "a")


async def test_translation_uses_deepl(db: Any, tmp_path: Path, monkeypatch: Any) -> None:
    api = Api(monkeypatch)
    config = AssistantAddon(enabled=True)
    assistant = Assistant(config, db, ZoneInfo("UTC"), None, "abc:fx", tmp_path)
    result = await assistant.ask("Übersetze Guten Morgen ins Englische", "de", "a")
    assert result.source == "translate" and result.text == "Good morning"
    assert api.requests[0].url.host == "api-free.deepl.com"
    assert json.loads(api.requests[0].content)["target_lang"] == "EN-GB"


@pytest.mark.parametrize(
    ("status", "key"), [(401, "bad_key"), (429, "rate_limited"), (500, "failed")]
)
async def test_provider_errors_are_explained(
    db: Any, tmp_path: Path, monkeypatch: Any, status: int, key: str
) -> None:
    api = Api(monkeypatch)
    api.status = status
    with pytest.raises(AssistantError, match=key):
        await make_assistant(db, tmp_path).ask("Frage", "de", "a")


# --- chat ----------------------------------------------------------------------------------


def addon_app(tmp_path: Path, **addons: Any) -> Any:
    app = make_app(tmp_path)
    object.__setattr__(app.config, "file", FileConfig.model_validate({"addons": addons}))
    from pi_guard.addons import Addons

    app.addons = Addons(app)
    return app


async def test_plain_text_goes_to_the_assistant_only_when_enabled(
    tmp_path: Path, monkeypatch: Any
) -> None:
    Api(monkeypatch)
    off = make_app(tmp_path / "a")
    await off.messenger.handle(msg("Was ist die Hauptstadt von Frankreich?", ADMIN))
    assert off.providers["telegram"].last().text != "Paris."
    off.db.close()
    settings = {"enabled": True, "provider": "custom", "base_url": "https://x/v1", "model": "m"}
    on = addon_app(tmp_path / "b", assistant=settings)
    on.addons.assistant._api_key = "k"
    await on.messenger.handle(msg("Was ist die Hauptstadt von Frankreich?", ADMIN))
    assert on.providers["telegram"].last().text == "Paris."
    await on.messenger.handle(msg("/gibtesnicht", ADMIN))
    assert on.providers["telegram"].last().text != "Paris."
    on.db.close()


async def test_action_needs_two_presses_in_chat(tmp_path: Path, monkeypatch: Any) -> None:
    from pi_guard.addons import remote as module
    from pi_guard.addons.remote import RunResult

    monkeypatch.setattr(module, "MIN_SECONDS_BETWEEN_STEPS", 0)
    app = addon_app(tmp_path, remote={"enabled": True, "actions": [ACTION.model_dump()]})
    ran: list[str] = []

    async def fake_run(action: RemoteAction) -> RunResult:
        ran.append(action.id)
        return RunResult(True, 0, "")

    app.addons.remote.run = fake_run
    provider = app.providers["telegram"]

    def press(reply: Reply, label_part: str) -> str:
        return next(b.command for r in reply.buttons for b in r if label_part in b.label)

    await app.messenger.handle(msg("/aktion", ADMIN))
    await app.messenger.handle(msg(press(provider.last(), "PC aus"), ADMIN, from_button=True))
    first = provider.last()
    assert not ran and "PC aus" in first.text
    await app.messenger.handle(msg(press(first, "Yes"), ADMIN, from_button=True))
    second = provider.last()
    assert not ran
    assert "cancel" in second.buttons[0][0].command  # the safe answer comes first now
    await app.messenger.handle(msg(second.buttons[1][0].command, ADMIN, from_button=True))
    assert ran == ["pc"]
    actions_logged = [r[0] for r in app.db.execute("SELECT action FROM audit")]
    assert "remote.run" in actions_logged
    app.db.close()


async def test_add_on_commands_explain_when_switched_off(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    for command in ("/dienste", "/analyse", "/smart", "/aktion", "/frage hallo"):
        await app.messenger.handle(msg(command, ADMIN))
        assert "Add-ons" in app.providers["telegram"].last().text
    app.db.close()


# --- WhatsApp ------------------------------------------------------------------------------

WEBHOOK = {"entry": [{"changes": [{"value": {
    "contacts": [{"wa_id": "4915112345678", "profile": {"name": "Anna"}}],
    "messages": [
        {"from": "4915112345678", "type": "text", "text": {"body": "/status"}},
        {"from": "4915112345678", "type": "image", "image": {"id": "1"}},
    ],
    "statuses": [{"id": "x"}],
}}]}]}  # fmt: skip


def test_webhook_parsing_and_signature() -> None:
    messages = parse_webhook(WEBHOOK)
    assert len(messages) == 1
    assert (messages[0].sender_id, messages[0].text) == ("4915112345678", "/status")
    assert messages[0].sender_name == "Anna" and messages[0].provider == "whatsapp"
    body = b'{"a":1}'
    good = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert valid_signature("secret", body, good)
    assert not valid_signature("other", body, good)
    assert not valid_signature("secret", b"changed", good)


async def test_whatsapp_sends_a_numbered_menu_and_falls_back_to_a_template(
    monkeypatch: Any,
) -> None:
    from pi_guard.messaging.base import Button
    from pi_guard.messaging.whatsapp import provider as module

    sent: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        if body["type"] == "text" and len(sent) == 2:
            return httpx.Response(400, json={"error": {"code": 131047}})
        return httpx.Response(200, json={})

    real = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handle), **kw))  # fmt: skip
    whatsapp = WhatsAppProvider("123", "token", "alarm")
    await whatsapp.send("49151", Reply("Hallo", buttons=((Button("Foto", "photo"),),)))
    assert sent[0]["text"]["body"] == "Hallo\n\n1  Foto"
    await whatsapp.send("49151", Reply("Alarm"))
    assert sent[-1]["type"] == "template" and sent[-1]["template"]["name"] == "alarm"


def test_whatsapp_needs_complete_settings(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="WHATSAPP"):
        make_app(tmp_path, whatsapp_enabled=True)


async def test_webhook_refuses_unsigned_messages(tmp_path: Path) -> None:
    client, app = await _client(
        tmp_path, whatsapp_enabled=True, whatsapp_phone_number_id="1",
        whatsapp_access_token="t", whatsapp_app_secret="secret", whatsapp_verify_token="v",
        whatsapp_admin_numbers=["+4915112345678"],
    )  # fmt: skip
    try:
        url = "/webhook/whatsapp"
        check = {"hub.verify_token": "v", "hub.challenge": "42"}
        assert await (await client.get(url, params=check)).text() == "42"
        assert (await client.get(url, params={"hub.verify_token": "x"})).status == 403
        body = json.dumps(WEBHOOK).encode()
        assert (await client.post(url, data=body)).status == 403
        signature = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        response = await client.post(url, data=body, headers={"X-Hub-Signature-256": signature})
        assert response.status == 200
        assert app.users.get("whatsapp", "4915112345678") is not None
    finally:
        await client.close()
        app.db.close()


# --- dashboard -----------------------------------------------------------------------------


async def test_addons_are_configured_in_the_dashboard(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        csrf = await _login(client)
        headers = {"X-CSRF-Token": csrf}
        data = await (await client.get("/api/addons")).json()
        assert {a["id"] for a in data["addons"]} == {
            "analytics", "smarthome", "remote", "assistant", "agents", "languages"}  # fmt: skip
        assert not any(a["enabled"] for a in data["addons"])
        section = {"enabled": True, "targets": [{"id": "nas", "name": "NAS", "kind": "ping",
                                                 "address": "192.168.178.20"}]}  # fmt: skip
        response = await client.put("/api/addons/heartbeat", json=section, headers=headers)
        assert response.status == 200
        again = {"enabled": True, "actions": [ACTION.model_dump()]}
        assert (await client.put("/api/addons/remote", json=again, headers=headers)).status == 200
        # Two saves without a restart in between keep both.
        written = yaml.safe_load((app.settings.data_dir / OVERRIDE_YAML).read_text())
        assert written["addons"]["heartbeat"]["targets"][0]["id"] == "nas"
        assert written["addons"]["remote"]["enabled"] is True
        assert written["addons"]["agents"]["enabled"] is True  # one add-on, "Server"
        data = await (await client.get("/api/addons")).json()
        running = {a["id"]: (a["enabled"], a["running"]) for a in data["addons"]}
        assert running["agents"] == (True, False)  # needs a restart
        bad = {"enabled": True, "targets": [{"id": "BAD ID", "name": "x", "address": "ftp://x"}]}
        response = await client.put("/api/addons/heartbeat", json=bad, headers=headers)
        assert response.status == 400
        assert (await client.put("/api/addons/nope", json={}, headers=headers)).status == 404
        assert (await client.get("/api/addons/remote/status")).status == 409
    finally:
        await client.close()
        app.db.close()


async def test_running_an_action_needs_the_typed_name(tmp_path: Path, monkeypatch: Any) -> None:
    from pi_guard.addons import remote as module
    from pi_guard.addons.remote import RunResult

    monkeypatch.setattr(module, "MIN_SECONDS_BETWEEN_STEPS", 0)
    client, app = await _client(tmp_path)
    try:
        app.addons.remote = RemoteActions([ACTION], tmp_path)
        ran: list[str] = []

        async def fake_run(action: RemoteAction) -> RunResult:
            ran.append(action.id)
            return RunResult(True, 0, "")

        app.addons.remote.run = fake_run
        headers = {"X-CSRF-Token": await _login(client)}

        async def post(path: str, body: dict[str, Any]) -> Any:
            return await client.post(f"/api/addons/remote/{path}", json=body, headers=headers)

        token = (await (await post("begin", {"id": "pc"})).json())["token"]
        second = await (await post("confirm", {"token": token})).json()
        assert second["name"] == "PC aus" and not ran
        response = await post("confirm", {"token": second["token"], "typed": "falsch"})
        assert response.status == 400 and not ran
        token = (await (await post("begin", {"id": "pc"})).json())["token"]
        second = await (await post("confirm", {"token": token})).json()
        done = await (await post("confirm", {"token": second["token"], "typed": "pc AUS"})).json()
        assert done["ok"] and ran == ["pc"]
    finally:
        await client.close()
        app.db.close()


def test_public_address_classification() -> None:
    tor = frozenset({"185.220.101.1"})
    assert classify("91.67.15.22", "91.67.15.22", tor, None) == "home"
    assert classify("185.220.101.1", "91.67.15.22", tor, "Some Org") == "tor"
    assert classify("5.6.7.8", "91.67.15.22", tor, "Mullvad VPN AB") == "vpn"
    assert classify("5.6.7.8", "91.67.15.22", tor, "Hetzner Online GmbH") == "vpn"
    assert classify("5.6.7.8", "91.67.15.22", tor, "Telefonica Germany") == "other"


async def test_connection_shows_the_vpn_device(tmp_path: Path, monkeypatch: Any) -> None:
    from pi_guard.web import api_addons

    async def no_lookup() -> None:
        return None

    monkeypatch.setattr(api_addons, "home_ip", no_lookup)
    client, app = await _client(tmp_path, dashboard_trusted_proxies=["127.0.0.1"])
    try:
        await _login(client)
        data = await (await client.get("/api/connection")).json()
        assert data["kind"] == "local"

        class Peers:
            def list(self) -> list[dict[str, str]]:
                return [{"address": "10.8.0.2", "name": "Handy Anna"}]

        app.dashboard.wg_peers = Peers()
        for ip, kind in (("10.8.0.2", "wireguard"), ("192.168.178.96", "lan")):
            response = await client.get("/api/connection", headers={"X-Forwarded-For": ip})
            data = await response.json()
            assert data["kind"] == kind
    finally:
        await client.close()
        app.db.close()


@pytest.mark.skipif(not shutil.which("mosquitto"), reason="needs mosquitto")
async def test_smart_home_with_a_real_broker(tmp_path: Path) -> None:
    import aiomqtt

    port = free_port()
    config = tmp_path / "mosquitto.conf"
    config.write_text(f"listener {port} 127.0.0.1\nallow_anonymous true\n")
    broker = subprocess.Popen(  # noqa: ASYNC220
        ["mosquitto", "-c", str(config)], stderr=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL)  # fmt: skip
    app = make_app(tmp_path, mqtt_host="127.0.0.1", mqtt_port=port)
    lamp = SmartDevice(id="lamp", name="Lampe", state_topic="home/lamp",
                       command_topic="home/lamp/set").model_dump()  # fmt: skip
    object.__setattr__(app.config, "file", FileConfig.model_validate(
        {"addons": {"smarthome": {"enabled": True, "devices": [lamp]}}}))  # fmt: skip
    home = SmartHome(app)
    task = None
    try:
        await asyncio.sleep(0.4)
        task = asyncio.create_task(home.run())
        async with aiomqtt.Client("127.0.0.1", port) as device:
            await device.subscribe("home/lamp/set")
            for _ in range(50):
                if home.connected:
                    break
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.2)
            await device.publish("home/lamp", "OFF")
            await device.publish("homeassistant/switch/x/config", json.dumps({
                "name": "Steckdose", "uniq_id": "plug", "stat_t": "z/plug", "cmd_t": "z/plug/set",
            }), retain=True)  # fmt: skip
            await asyncio.sleep(0.3)
            await device.publish("z/plug", "ON")
            await asyncio.sleep(0.3)
            rows = {r["id"]: r for r in home.overview()}
            assert rows["lamp"]["on"] is False
            assert rows["d_plug"]["on"] is True and rows["d_plug"]["discovered"]
            await home.switch("lamp", None)
            async with asyncio.timeout(3):
                message = await anext(device.messages.__aiter__())
            assert bytes(message.payload) == b"ON"
    finally:
        if task:
            task.cancel()
        broker.terminate()
        broker.wait()
        app.db.close()


def test_an_old_heartbeat_switch_turns_on_the_whole_server_addon() -> None:
    from pi_guard.config.files import AddonSettings

    old = AddonSettings.model_validate({"heartbeat": {"enabled": True}})
    assert old.agents.enabled and old.heartbeat.enabled
    both_off = {"heartbeat": {"enabled": False}, "agents": {"enabled": False}}
    off = AddonSettings.model_validate(both_off)
    assert not (off.agents.enabled or off.heartbeat.enabled)
