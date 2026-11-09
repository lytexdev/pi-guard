from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from pi_guard.core.events import Event, EventType
from pi_guard.core.modes import Mode
from pi_guard.web.push import WebPush

pytest.importorskip("pywebpush")

SUB = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "x" * 87, "auth": "y" * 22}}


def test_vapid_key_is_created_once_and_private(tmp_path: Path, db: Any) -> None:
    push = WebPush(db, tmp_path / "vapid.pem", "mailto:a@b.c")
    key = push.public_key
    raw = base64.urlsafe_b64decode(key + "==")
    assert len(raw) == 65 and raw[0] == 4  # uncompressed P-256 point
    assert (tmp_path / "vapid.pem").stat().st_mode & 0o077 == 0
    assert WebPush(db, tmp_path / "vapid.pem", "mailto:a@b.c").public_key == key


def test_subscriptions_are_validated(tmp_path: Path, db: Any) -> None:
    push = WebPush(db, tmp_path / "vapid.pem", "mailto:a@b.c")
    with pytest.raises(ValueError):
        push.subscribe({"endpoint": "http://insecure", "keys": SUB["keys"]}, "de", "admin")
    push.subscribe(SUB, "de", "admin")
    assert push.subscriptions()[0]["language"] == "de"
    assert push.unsubscribe(SUB["endpoint"]) and push.subscriptions() == []


async def test_alerts_are_pushed_in_each_language(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from .helpers import make_app

    app = make_app(tmp_path)
    app.webpush = WebPush(app.db, tmp_path / "vapid.pem", "mailto:a@b.c")
    app.webpush.subscribe(SUB, "de", "admin")
    sent: list[dict[str, str]] = []
    monkeypatch.setattr(app.webpush, "_send_one", lambda sub, payload: sent.append(payload) or True)
    app.modes.set_manual(Mode.AWAY)
    await app.notifier.handle(Event(EventType.MOTION_DETECTED, camera="living_room"))
    for task in list(app.notifier._push_tasks):
        await task
    assert sent and sent[0]["body"].startswith("Bewegung erkannt")
    assert "photo" not in sent[0] and sent[0]["priority"] == "normal"
    app.db.close()


def test_gone_subscriptions_are_removed(tmp_path: Path, db: Any, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import pywebpush

    push = WebPush(db, tmp_path / "vapid.pem", "mailto:a@b.c")
    push.subscribe(SUB, "en", "admin")

    class Response:
        status_code = 410

    def gone(**kwargs: Any) -> None:
        raise pywebpush.WebPushException("gone", response=Response())

    monkeypatch.setattr(pywebpush, "webpush", gone)
    assert not push._send_one(push.subscriptions()[0], {"body": "x"})
    assert push.subscriptions() == []


def test_a_button_token_works_once_and_not_for_ever(tmp_path: Path, db: Any) -> None:
    from datetime import timedelta

    from pi_guard.core.timeutil import utcnow

    push = WebPush(db, tmp_path / "vapid.pem", "mailto:a@b.c")
    token = push.action_token("admin")
    assert token not in str(list(db.execute("SELECT * FROM push_action_tokens")))  # hashed
    assert push.use_action_token(token) == "admin"
    assert push.use_action_token(token) is None  # once
    old = push.action_token("admin")
    db.execute(
        "UPDATE push_action_tokens SET expires = ?", ((utcnow() - timedelta(1)).isoformat(),)
    )
    assert push.use_action_token(old) is None


async def test_only_admins_get_buttons(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from .helpers import make_app

    app = make_app(tmp_path)
    app.webpush = WebPush(app.db, tmp_path / "vapid.pem", "mailto:a@b.c")
    app.webpush.subscribe(SUB, "de", "admin")
    app.webpush.subscribe({**SUB, "endpoint": "https://push.example/viewer"}, "en", "lena")
    sent: dict[str, dict[str, str]] = {}

    def record(sub: dict[str, str], payload: dict[str, str]) -> bool:
        sent[sub["username"]] = payload
        return True

    monkeypatch.setattr(app.webpush, "_send_one", record)
    await app.webpush.send({"de": {"body": "x"}, "en": {"body": "x"}}, lambda name: name == "admin")
    assert "token" in sent["admin"] and "token" not in sent["lena"]
    app.db.close()


async def test_a_push_button_acknowledges_without_a_login(tmp_path: Path) -> None:
    from aiohttp.test_utils import TestClient, TestServer

    from pi_guard.core.events import Event, EventType
    from pi_guard.web import auth
    from pi_guard.web.server import Dashboard

    from .helpers import make_app

    app = make_app(tmp_path, dashboard_enabled=True, dashboard_tls=False)
    app.dashboard = Dashboard(app)
    app.dashboard.auth.change_credentials(auth.DEFAULT_PASSWORD, "admin", "correct horse battery")
    app.webpush = WebPush(app.db, tmp_path / "vapid.pem", "mailto:a@b.c")
    client = TestClient(TestServer(app.dashboard.web))
    await client.start_server()
    try:
        app.escalation.record(Event(EventType.MOTION_DETECTED, camera="living_room"), None)
        token = app.webpush.action_token("admin")
        tap = await client.post("/api/push/action", json={"action": "ack", "token": token})
        assert tap.status == 200 and app.escalation.open_alerts() == []
        again = await client.post("/api/push/action", json={"action": "ack", "token": token})
        assert again.status == 403
        viewer = app.webpush.action_token("someone-else")
        refused = await client.post("/api/push/action", json={"action": "ack", "token": viewer})
        assert refused.status == 403  # unknown names are viewers
    finally:
        await client.close()
        app.db.close()
