"""The module stays for later; pi-guard does not use it while the statistics are off.

Anonymous statistics: off by default, numbers only, sent once a week."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from pi_guard.core.timeutil import utcnow
from pi_guard.diagnostics import contribution
from pi_guard.diagnostics.contribution import (
    CORRECTIONS_KEY,
    ENABLED_KEY,
    SENT_KEY,
    Contribution,
    build_report,
    record_correction,
)

from .helpers import make_app

URL = "https://stats.example.org/v1/report"


@pytest.fixture
def app(tmp_path: Path) -> Any:
    app = make_app(tmp_path, support_url=URL)
    yield app
    app.db.close()


def test_the_report_holds_only_counts(app: Any) -> None:
    now = utcnow()
    app.db.add_event(now, "known_person", "living_room",
                     {"bodies": 1, "faces": 1, "face_px": [60], "scores": [0.71], "name": "Lena",
                      "snapshot": "person_x.jpg", "detector": "yolox", "pictures": 2})  # fmt: skip
    app.db.add_event(now, "unknown_person", "living_room",
                     {"bodies": 7, "face_px": [30, 170], "scores": [0.12, -0.1]})  # fmt: skip
    app.db.add_event(now - timedelta(days=8), "motion_detected", "living_room", {})  # too old
    record_correction(app, True, 0.33)
    record_correction(app, False, 0.52)
    report = build_report(app)
    text = json.dumps(report)
    for private in ("Lena", "person_x", "living_room", now.isoformat()[:13]):
        assert private not in text
    assert report["events"] == {"known_person": 1, "unknown_person": 1}
    assert report["face_px"][7] == 1 and report["face_px"][3] == 1 and report["face_px"][-1] == 1
    assert report["scores"]["known_person"][14] == 1  # 0.70-0.75
    assert report["scores"]["unknown_person"][2] == 1 and report["scores"]["unknown_person"][0] == 1
    assert report["bodies"] == {"1": 1, "5+": 1}
    assert report["corrections"]["is"][6] == 1 and report["corrections"]["not"][10] == 1
    assert report["setup"]["recognition"] is False and report["week"].startswith(str(now.year))


def test_nothing_is_sent_unless_switched_on_with_a_receiver(app: Any, tmp_path: Path) -> None:
    assert not Contribution(app).due()  # off by default
    app.db.set(ENABLED_KEY, True)
    assert Contribution(app).due()
    app.db.set(SENT_KEY, (utcnow() - timedelta(days=2)).isoformat())
    assert not Contribution(app).due()  # once a week
    without = make_app(tmp_path / "other")
    without.db.set(ENABLED_KEY, True)
    assert not Contribution(without).due()
    without.db.close()


async def test_a_sent_report_forgets_the_corrections(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    received: list[dict[str, Any]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        received.append(json.loads(request.content))
        return httpx.Response(204)

    real = httpx.AsyncClient
    monkeypatch.setattr(
        contribution.httpx, "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(answer), **kw),
    )  # fmt: skip
    record_correction(app, True, 0.4)
    await Contribution(app).send()
    assert received[0]["corrections"]["is"][8] == 1
    assert app.db.get(CORRECTIONS_KEY) == {} and app.db.get(SENT_KEY)


def test_the_receiver_must_be_https() -> None:
    from pydantic import ValidationError

    from pi_guard.config.settings import Settings

    assert Settings(support_url=" ").support_url is None
    with pytest.raises(ValidationError):
        Settings(support_url="http://stats.example.org/report")


def test_crashes_keep_only_their_shape(app: Any) -> None:
    from pi_guard.diagnostics.contribution import clean_crash, keep_crash

    crash = clean_crash({"exception": "java.lang.IllegalStateException", "app": "1.4.0", "sdk": 34,
                         "message": "token pg_secret at 192.168.178.20",
                         "frames": ["de.lytex.Vpn.up:120", "rm -rf /", "x" * 300]})  # fmt: skip
    assert crash is not None and crash["frames"] == ["de.lytex.Vpn.up:120"]
    assert (crash["exception"], crash["app"], crash["sdk"]) == ("java.lang.IllegalStateException",
                                                                "1.4.0", 34)  # fmt: skip
    assert "message" not in crash
    assert clean_crash({"exception": "bad name; drop", "frames": []}) is None
    for _ in range(25):
        keep_crash(app, crash)
    report = build_report(app)
    assert report["report"] == 2 and len(report["crashes"]) == 20  # bounded
    assert "pg_secret" not in json.dumps(report)


async def test_a_crash_from_an_older_app_is_not_kept_while_statistics_are_off(
    tmp_path: Path,
) -> None:
    from .test_mobile import _paired

    client, app, _, bearer = await _paired(tmp_path)
    try:
        crash = {"exception": "java.lang.NullPointerException", "frames": ["de.lytex.A.b:1"],
                 "app": "1.4.0", "sdk": 34}  # fmt: skip
        assert (await client.post("/api/mobile/crash", json=crash)).status == 401
        assert (await client.post("/api/mobile/crash", json=crash, headers=bearer)).status == 200
        assert app.db.get("contribution.crashes") is None
    finally:
        await client.close()
        app.db.close()
