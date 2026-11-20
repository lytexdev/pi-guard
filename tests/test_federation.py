"""Linked households: the code, the proofs, what passes the tunnel, and two Pis end to end."""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from pi_guard.federation import protocol as p
from pi_guard.federation.store import FederationError

from .test_web import PASSWORD, _client, _login


def test_a_code_carries_url_pin_key_and_token_and_nothing_else_passes() -> None:
    code = p.Code("wss://home.example.org:8444/federation/ws", "ab" * 32, b"k" * 32,
                  "t" * 32, "Oma  Helga\n")  # fmt: skip
    back = p.parse_code(code.encode())
    assert (back.url, back.pin, back.key, back.token, back.name) == (
        code.url, code.pin, code.key, code.token, "Oma Helga")  # fmt: skip
    good = ("wss://h:1/federation/ws", "ab" * 32, b"k" * 32, "t" * 32, "")
    wrong = [("https://evil/federation/ws", *good[1:]), (good[0], "zz" * 32, *good[2:]),
             (*good[:2], b"k" * 31, *good[3:])]  # fmt: skip
    for bad in (
        "",
        "PGF1.",
        "PGF1.e30",
        "x" + code.encode(),
        *(p.Code(*w).encode() for w in wrong),
    ):
        with pytest.raises(p.ProtocolError):
            p.parse_code(bad)


def test_a_proof_is_bound_to_side_nonces_and_key() -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    me, other = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    a, b, key = p.new_nonce(), p.new_nonce(), p.public_bytes(other)
    sig = p.sign(me, p.proof_message("helper", a, b, key))
    public = p.public_bytes(me)
    assert p.verify(public, sig, p.proof_message("helper", a, b, key))
    assert not p.verify(public, sig, p.proof_message("helped", a, b, key))  # other role
    assert not p.verify(public, sig, p.proof_message("helper", b, a, key))  # swapped nonces
    assert not p.verify(key, sig, p.proof_message("helper", a, b, key))  # other key
    assert not p.verify(public, "not base64!", p.proof_message("helper", a, b, key))


@pytest.mark.parametrize(
    ("path", "role", "extras", "allowed"),
    [
        ("/", "admin", [], False),  # the page and scripts come from the helper's own Pi
        ("/static/js/app.js", "viewer", [], False),
        ("/api/overview", "viewer", [], True),
        ("/api/session", "admin", [], True),
        ("/api/federation", "admin", ["alerts", "cameras", "medical"], False),
        ("/api/federation/links/1", "admin", [], False),
        ("/api/account/credentials", "admin", [], False),
        ("/api/dashboard-users", "admin", [], False),
        ("/api/keyfiles", "admin", [], False),
        ("/api/tokens", "admin", [], False),
        ("/api/export/data", "admin", [], False),
        ("/api/backup/run", "admin", [], False),
        ("/api/live/door/video", "admin", ["cameras"], False),
        ("/api/live/door/snapshot", "admin", [], False),
        ("/api/live/door/snapshot", "admin", ["cameras"], True),
        ("/api/media/snapshots/a.jpg", "admin", [], False),
        ("/api/events/3/faces/0/crop", "admin", [], False),
        ("/api/events/3", "admin", [], True),
        ("/api/medical", "admin", ["alerts"], False),
        ("/api/medical", "admin", ["medical"], True),
        ("/get/token/android", "admin", [], False),
        ("/static/../pi-guard.db", "admin", [], False),
        ("/api/overview", "nobody", [], False),
    ],
)
def test_what_a_helper_may_reach(path: str, role: str, extras: list[str], allowed: bool) -> None:
    assert p.tunnel_allowed(path, role, extras) is allowed


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def _until(check: Callable[[], bool], seconds: float = 8) -> None:
    for _ in range(int(seconds * 20)):
        if check():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("timed out")


@pytest.fixture
async def two(tmp_path: Path) -> AsyncIterator[tuple[Any, Any, Any, Any]]:
    """A helper Pi listening for links, and a helped Pi with its dashboard on a real port."""
    port = _free_port()
    helper_client, helper = await _client(tmp_path / "helper", federation_enabled=True,
                                          federation_listen=True, federation_port=port,
                                          federation_address="127.0.0.1")  # fmt: skip
    helped_client, helped = await _client(tmp_path / "helped", federation_enabled=True)
    helped.settings.dashboard_host = "127.0.0.1"
    helped.settings.dashboard_port = helped_client.server.port
    listen = asyncio.create_task(helper.dashboard.federation.listen())
    run = asyncio.create_task(helped.dashboard.federation.run())
    await asyncio.sleep(0.3)
    try:
        yield helper_client, helper, helped_client, helped
    finally:
        listen.cancel()
        run.cancel()
        await asyncio.gather(listen, run, return_exceptions=True)
        for client, app in ((helper_client, helper), (helped_client, helped)):
            await client.close()
            app.db.close()


async def test_two_households_link_and_the_helper_reaches_only_what_was_granted(two) -> None:  # type: ignore[no-untyped-def]
    _, helper, helped_client, helped = two
    hub, home = helper.dashboard.federation, helped.dashboard.federation
    text = hub.invite()
    code = p.parse_code(text)

    # A changed pin (another certificate) or another key in the code: nothing is linked.
    forged = p.Code(code.url, "00" * 32, code.key, code.token, code.name)
    with pytest.raises(FederationError, match="unreachable"):
        await home.join(forged, "admin", ["alerts"])
    wrong_key = p.Code(code.url, code.pin, b"x" * 32, code.token, code.name)
    with pytest.raises(FederationError, match="wrong_helper"):
        await home.join(wrong_key, "admin", ["alerts"])
    assert hub.store.links() == [] and home.store.links() == []

    link_id = await home.join(code, "admin", ["alerts"])
    assert home.store.get(link_id).side == "helped"
    [helping] = hub.store.links("helper")
    with pytest.raises(FederationError):  # the code works once
        await home.join(code, "viewer", [])

    await _until(lambda: helping.id in hub.peers)
    await _until(lambda: helping.id in hub.status)
    assert hub.status[helping.id]["version"]

    session = await hub.request(helping.id, "GET", "/api/session", "", b"")
    body = json.loads(p.unb64(session["b"]))
    assert session["s"] == 200 and body["authenticated"] and body["role"] == "admin"
    assert (await hub.request(helping.id, "GET", "/api/overview", "", b""))["s"] == 200
    for blocked in ("/api/medical", "/api/federation", "/api/dashboard-users"):
        assert (await hub.request(helping.id, "GET", blocked, "", b""))["s"] == 403
    change = json.dumps({"current": "x", "username": "evil", "password": "y" * 12}).encode()
    answer = await hub.request(helping.id, "POST", "/api/account/credentials",
                               "application/json", change)  # fmt: skip
    assert answer["s"] == 403
    assert helped.dashboard.auth.account()["username"] == "admin"

    # Something that needs the password: never through the link.
    renew = json.dumps({"password": PASSWORD}).encode()
    answer = await hub.request(helping.id, "PUT", "/api/network/wifi/profiles/home",
                               "application/json", renew)  # fmt: skip
    assert answer["s"] == 403 and p.unb64(answer["b"]) == b"web.fed_needs_local"

    # The loopback secret is the only way in as a helper: a guess from outside is refused.
    guess = {"X-PiGuard-Federation": "guess", "X-PiGuard-Federation-Link": str(link_id)}
    assert (await helped_client.get("/api/overview", headers=guess)).status == 401

    # Only looking: changing the mode is refused by the helped dashboard itself.
    home.store.set_grants(link_id, "viewer", [])
    mode = json.dumps({"mode": "away"}).encode()
    assert (await hub.request(helping.id, "POST", "/api/mode", "application/json", mode))[
        "s"
    ] == 403

    # The helped side ends it: the helper is gone at once.
    h = {"X-CSRF-Token": await _login(helped_client)}
    gone = await helped_client.delete(f"/api/federation/links/{link_id}",
                                      json={"password": PASSWORD}, headers=h)  # fmt: skip
    assert gone.status == 200
    await _until(lambda: helping.id not in hub.peers)


async def test_the_helper_opens_the_household_through_its_own_dashboard(two) -> None:  # type: ignore[no-untyped-def]
    helper_client, helper, _, helped = two
    hub, home = helper.dashboard.federation, helped.dashboard.federation
    await home.join(p.parse_code(hub.invite()), "admin", [])
    [helping] = hub.store.links("helper")
    await _until(lambda: helping.id in hub.peers)

    base = f"/fed/{helping.id}"
    assert (await helper_client.get(f"{base}/api/session")).status == 401  # logged in first
    csrf = await _login(helper_client)
    boot = await helper_client.get(f"{base}/")
    assert boot.status == 200 and "fed-boot.js" in await boot.text()
    assert (await helper_client.get(f"{base}/fed-sw.js")).status == 200
    page = await helper_client.get(f"{base}/app/")
    assert page.status == 200 and "/static/js/app.js" in await page.text()
    session = await (await helper_client.get(f"{base}/api/session")).json()
    assert session["csrf"] == csrf and session["federation"]["id"] == helping.id
    assert (await helper_client.post(f"{base}/api/mode", json={"mode": "away"})).status == 403
    done = await helper_client.post(f"{base}/api/mode", json={"mode": "away"},
                                    headers={"X-CSRF-Token": csrf})  # fmt: skip
    assert done.status == 200, await done.text()
    assert helped.modes.effective.value == "AWAY"
    audit = [r[0] for r in helped.db.execute("SELECT actor FROM audit WHERE action LIKE 'mode%'")]
    assert any(a.startswith("web:fed:") for a in audit)
    assert (await helper_client.get("/fed/999/api/session")).status == 404


async def test_alarms_reach_the_helper_only_when_allowed_and_an_sos_always(two) -> None:  # type: ignore[no-untyped-def]
    from pi_guard.core.events import Event, EventType

    _, helper, _, helped = two
    hub, home = helper.dashboard.federation, helped.dashboard.federation
    told: list[tuple[str, bool]] = []

    async def notify(text: str, *_: Any, sos: bool = False, **__: Any) -> None:
        told.append((text, sos))

    helper.messenger.notify_admins = notify
    link_id = await home.join(p.parse_code(hub.invite()), "viewer", [])
    [helping] = hub.store.links("helper")
    await _until(lambda: link_id in home.connected and helping.id in hub.peers)
    told.clear()  # "new linked household"

    helped.bus.publish(Event(EventType.UNKNOWN_PERSON, camera="door"))
    await asyncio.sleep(0.3)
    assert told == []  # alarms were not granted
    await home.sos()
    await _until(lambda: bool(told))
    assert told[0][1] and "SOS" in told[0][0]

    home.store.set_grants(link_id, "viewer", ["alerts"])
    helped.bus.publish(Event(EventType.UNKNOWN_PERSON, camera="door"))
    await _until(lambda: len(told) == 2)
    assert not told[1][1] and hub.alerts[helping.id][0]["kind"] == "unknown_person"


def test_a_household_answer_never_becomes_a_page_on_the_helper() -> None:
    from pi_guard.web.api_federation import _shown

    assert _shown("application/json; charset=utf-8", "") == ("application/json", "")
    assert _shown("image/jpeg", 'inline; filename="a.jpg"') == (
        "image/jpeg",
        'inline; filename="a.jpg"',
    )
    for evil in ("text/html", "image/svg+xml", "application/xhtml+xml", "text/javascript", ""):
        assert _shown(evil, "inline") == ("application/octet-stream", "attachment")
    assert _shown("application/pdf", 'attachment; filename="sheet.pdf"') == (
        "application/octet-stream", 'attachment; filename="sheet.pdf"')  # fmt: skip


async def test_households_reach_each_others_admins_as_contacts(two, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pi_guard.federation import service
    from pi_guard.messaging.base import ProviderUnavailableError, Reply

    _, helper, helped_client, helped = two
    hub, home = helper.dashboard.federation, helped.dashboard.federation
    heard: dict[str, list[tuple[str, bool]]] = {"helper": [], "helped": []}

    def listener(side: str) -> Callable[..., Any]:
        async def notify(text: str, *_: Any, sos: bool = False, **__: Any) -> None:
            heard[side].append((text, sos))

        return notify

    helper.messenger.notify_admins = listener("helper")
    helped.messenger.notify_admins = listener("helped")
    link_id = await home.join(p.parse_code(hub.invite()), "viewer", [])
    [helping] = hub.store.links("helper")
    await _until(lambda: link_id in home.connected and helping.id in hub.peers)
    heard["helper"].clear()  # "new linked household"

    # Both ways, through the provider every contact uses; only words, cleaned.
    await helped.providers["household"].send(str(link_id), Reply("Bitte \x07melden", tone="alert"))
    await _until(lambda: bool(heard["helper"]))
    text, urgent = heard["helper"][0]
    assert "Bitte melden" in text and urgent
    await helper.providers["household"].send(str(helping.id), Reply("Alles gut?"))
    await _until(lambda: bool(heard["helped"]))
    assert "Alles gut?" in heard["helped"][0][0] and not heard["helped"][0][1]

    with pytest.raises(ProviderUnavailableError):
        await helped.providers["household"].send("999", Reply("x"))

    # A flood from one household is cut off (one note came already in this window).
    monkeypatch.setattr(service, "NOTES_PER_WINDOW", 2)
    for n in range(4):
        await helper.providers["household"].send(str(helping.id), Reply(f"spam {n}"))
    await asyncio.sleep(0.3)
    assert len(heard["helped"]) == 2

    # The household is a way like a messenger: offered to contacts, valid as an address.
    h = {"X-CSRF-Token": await _login(helped_client)}
    data = await (await helped_client.get("/api/contacts")).json()
    assert "household" in data["providers"] and data["households"][0]["id"] == link_id
    contact = {"name": "Familie", "channels": [{"provider": "household", "address": str(link_id)}],
               "share": {"sos": ["location"]}}  # fmt: skip
    saved = await helped_client.post("/api/contacts", json={"contact": contact}, headers=h)
    assert saved.status == 200
    assert helped.escalation.contacts.recipients("sos")[0][:2] == ("household", str(link_id))


def test_the_link_port_must_not_be_the_block_page_or_dashboard() -> None:
    from types import SimpleNamespace

    from pi_guard.federation.service import port_taken_by

    base = {"federation_port": 8444, "dashboard_port": 8443, "blockpage_ip": None}
    assert port_taken_by(SimpleNamespace(**base)) is None
    assert port_taken_by(SimpleNamespace(**{**base, "blockpage_ip": "192.168.1.20"})) == "blockpage"
    assert port_taken_by(SimpleNamespace(**{**base, "federation_port": 8443})) == "dashboard"


async def test_a_taken_port_is_reported_not_retried_and_cannot_be_saved(tmp_path: Path) -> None:
    client, app = await _client(tmp_path, federation_enabled=True, federation_listen=True,
                                blockpage_ip="192.168.1.20")  # fmt: skip
    try:
        federation = app.dashboard.federation
        task = asyncio.create_task(federation.listen())
        await asyncio.sleep(0.2)
        assert not task.done() and federation.listen_problem == "web.fed_port_blockpage"
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        h = {"X-CSRF-Token": await _login(client)}
        data = await (await client.get("/api/federation")).json()
        assert data["listen_problem"] == "web.fed_port_blockpage"
        bad = await client.put("/api/settings", json={"federation_port": 8088}, headers=h)
        assert bad.status == 400 and await bad.text() == "web.fed_port_blockpage"
        good = await client.put("/api/settings", json={"federation_port": 8445}, headers=h)
        assert good.status == 200, await good.text()
    finally:
        await client.close()
        app.db.close()


@pytest.mark.parametrize(
    ("typed", "kept"),
    [("https://Pi.Example.org/", "pi.example.org"), ("pi.example.org:8445", "pi.example.org"),
     ("wss://pi.example.org:8445/federation/ws", "pi.example.org"), ("203.0.113.7", "203.0.113.7"),
     ("[2001:db8::1]:8445", "2001:db8::1"), ("", "")],
)  # fmt: skip
def test_the_public_address_keeps_only_the_name(typed: str, kept: str) -> None:
    from pi_guard.config.settings import Settings

    settings = Settings(_env_file=None, telegram_enabled=False, federation_address=typed)  # type: ignore[call-arg]
    assert settings.federation_address == kept


async def test_a_pasted_url_as_address_still_makes_a_code_the_other_pi_reads(
    tmp_path: Path,
) -> None:
    # The owner's Pi had "https://…" here, and every code it made was "not a valid code".
    client, app = await _client(tmp_path, federation_enabled=True, federation_listen=True,
                                federation_port=8445,
                                federation_address="https://pi.example.org")  # fmt: skip
    try:
        code = p.parse_code(app.dashboard.federation.invite())
        assert code.url == "wss://pi.example.org:8445/federation/ws"
    finally:
        await client.close()
