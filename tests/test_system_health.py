"""The System page: health checks of the running service and what the device is."""

from __future__ import annotations

from pathlib import Path

from aiohttp import CookieJar
from aiohttp.test_utils import TestClient

from pi_guard.diagnostics import health

from .test_web import _client, _login


async def test_the_owner_sees_checks_and_hardware_a_viewer_does_not(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        await _login(client)
        data = await (await client.get("/api/system/health")).json()
        ids = {c["id"] for c in data["checks"]}
        assert {"service", "camera", "storage", "memory", "cpu", "database", "backup"} <= ids
        assert all(c["level"] in ("ok", "warn", "fail", "skip") for c in data["checks"])
        names = {c["params"].get("name") for c in data["checks"] if c["id"] == "messenger"}
        assert not names & {"household", "mobile"}  # not messengers that can be down
        database = next(c for c in data["checks"] if c["id"] == "database")
        assert database["level"] == "ok"
        hw = data["hardware"]
        assert hw["cores"] >= 1 and hw["version"] and {d["label"] for d in hw["disks"]} == {
            "data", "recordings"}  # fmt: skip

        app.dashboard.auth.add_user("guest", "guest password 1", "viewer")
        viewer = TestClient(client.server, cookie_jar=CookieJar(unsafe=True))
        await viewer.start_server()
        try:
            await viewer.post(
                "/api/login", json={"username": "guest", "password": "guest password 1"}
            )
            assert (await viewer.get("/api/system/health")).status == 403
        finally:
            await viewer.close()
    finally:
        await client.close()
        app.db.close()


def test_a_failing_check_does_not_hide_the_others(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from .test_web import make_app

    app = make_app(tmp_path)

    def broken(_: object) -> list[dict[str, object]]:
        raise RuntimeError("boom")

    monkeypatch.setattr(health, "_database", broken)
    try:
        checks = health.checks(app)
        assert {"id": "database", "level": "fail", "params": {"error": "RuntimeError"}} in checks
        assert any(c["id"] == "storage" for c in checks)
    finally:
        app.db.close()


def test_the_pi_firmware_word_names_power_and_heat(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    word = tmp_path / "throttled"
    # Undervoltage and throttled now, and both since boot.
    word.write_text("50005\n")
    monkeypatch.setattr(health, "_THROTTLED", (tmp_path / "missing", word))
    assert health.throttled() == {"now": ["undervoltage", "throttled"],
                                  "since_boot": ["undervoltage", "throttled"]}  # fmt: skip
    word.write_text("0x0")
    assert health.throttled() == {"now": [], "since_boot": []}
    monkeypatch.setattr(health, "_THROTTLED", (tmp_path / "missing",))
    assert health.throttled() is None
