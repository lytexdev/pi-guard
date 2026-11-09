"""Anonymous statistics: off by default, numbers only, sent once a week."""

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
