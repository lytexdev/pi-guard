"""pgtrain without a GPU: reports, thresholds, the training maths and the local page."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import torch
from pi_guard.diagnostics.contribution import PX_BINS, SCORE_BINS

from pgtrain import faces, persons, reports
from pgtrain.persons import distill_loss, letterbox, match, score, split
from pgtrain.yolox_model import YOLOX

RNG = np.random.default_rng(3)
MODELS = (
    Path(os.environ.get("PGTRAIN_HOME", Path.home() / ".local/share/pi-guard-train")) / "models"
)


def unit(v: np.ndarray) -> np.ndarray:
    return (v / np.linalg.norm(v)).astype(np.float32)


def a_report(**changes: Any) -> dict[str, Any]:
    report = {
        "report": 1, "pi_guard": "0.1.0", "week": "2026-W46",
        "setup": {"cameras": "1", "resolutions": ["1280x720"], "recognition": True, "people": "2",
                  "threshold": 0.4, "margin": 0.06},
        "events": {"motion_detected": 40, "person_detected": 6},
        "face_px": [0] * PX_BINS,
        "scores": {t: [0] * SCORE_BINS
                   for t in ("person_detected", "unknown_person", "known_person")},
        "bodies": {"1": 5, "5+": 1}, "pictures": {"1": 4, "3": 2}, "detectors": {"yolox": 6},
        "corrections": {"is": [0] * SCORE_BINS, "not": [0] * SCORE_BINS},
    }  # fmt: skip
    report.update(changes)
    return report


# --- reports ---------------------------------------------------------------------------------


def test_a_report_from_a_pi_is_accepted_and_extra_fields_are_dropped() -> None:
    checked = reports.check(
        a_report(extra="a name", setup={**a_report()["setup"], "ip": "1.2.3.4"})
    )
    assert "extra" not in checked and "ip" not in checked["setup"]


@pytest.mark.parametrize(
    "bad",
    [
        {"report": 2},
        {"week": "today"},
        {"face_px": [0] * 3},
        {"face_px": [-1] * PX_BINS},
        {"events": {"<script>": 1}},
        {"bodies": {"12": 1}},
        {"setup": {"cameras": "seven", "people": "1"}},
    ],
)
def test_anything_else_is_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(reports.BadReport):
        reports.check(a_report(**bad))


async def test_the_receiver_stores_reports_without_addresses(
    tmp_path: Path, aiohttp_client: Any
) -> None:
    client = await aiohttp_client(reports.receiver(tmp_path))
    assert (await client.post("/v1/report", json=a_report())).status == 204
    assert (await client.post("/v1/report", json={"report": 1})).status == 400
    assert (await client.post("/v1/report", data=b"x" * (reports.MAX_BYTES + 1))).status == 413
    stored = list(tmp_path.glob("2026-W46/*.json"))
    assert len(stored) == 1 and "127.0.0.1" not in stored[0].read_text()


def test_reports_add_up(tmp_path: Path) -> None:
    corrections = {"is": [0] * SCORE_BINS, "not": [0] * SCORE_BINS}
    corrections["is"][12] = 3
    for week in ("2026-W45", "2026-W46"):
        (tmp_path / week).mkdir()
        (tmp_path / week / "a.json").write_text(
            json.dumps(a_report(week=week, corrections=corrections))
        )
    (tmp_path / "2026-W46" / "broken.json").write_text("{")
    total = reports.summary(reports.load(tmp_path))
    assert total["reports"] == 2 and total["events"]["person_detected"] == 12
    assert total["corrections"]["is"][12] == 6


# --- thresholds ------------------------------------------------------------------------------


def family(people: int = 4, photos: int = 8, noise: float = 0.7) -> dict[str, list[np.ndarray]]:
    result = {}
    for p in range(people):
        base = unit(RNG.normal(size=128))
        result[f"p{p}"] = [unit(base + noise * unit(RNG.normal(size=128))) for _ in range(photos)]
    return result


def test_each_photo_is_judged_against_the_others() -> None:
    genuine, impostor = faces.leave_one_out(family())
    assert len(genuine) == len(impostor) == 32
    assert min(genuine) > max(impostor)  # these synthetic people are easy to tell apart


def test_a_suggested_threshold_keeps_strangers_out() -> None:
    genuine, impostor = faces.leave_one_out(family(6, 10, 1.0))
    best = faces.recommend(genuine, impostor, 0.06)
    assert best is not None
    after = faces.rates(genuine, impostor, best, 0.06)
    assert after["stranger_taken_for_family"] <= faces.TARGET_FALSE_ACCEPT
    assert faces.recommend(genuine, [], 0.06) is None
    assert faces.THRESHOLD_RANGE[0] <= best <= faces.THRESHOLD_RANGE[1]


def test_histograms_become_scores_again() -> None:
    counts = [0] * SCORE_BINS
    counts[8] = 2
    assert list(faces.from_histogram(counts, SCORE_BINS)) == [pytest.approx(0.425)] * 2


# --- person detector training ----------------------------------------------------------------


def test_the_checking_share_stays_the_same() -> None:
    paths = [Path(f"{i}.jpg") for i in range(2000)]
    train, val = split(paths)
    assert split(list(reversed(paths)))[1] == list(reversed(val))
    assert 150 < len(val) < 250 and not set(train) & set(val)


def test_pictures_are_letterboxed_like_on_the_pi() -> None:
    padded, ratio = letterbox(np.zeros((720, 1280, 3), np.uint8))
    assert padded.shape == (416, 416, 3) and ratio == pytest.approx(0.325)
    assert padded[300, 0, 0] == persons.PAD  # below the picture


def test_the_student_learns_towards_the_teacher() -> None:
    torch.manual_seed(0)
    teacher = torch.rand(2, 3549, 85)
    teacher[..., :4] = torch.randn(2, 3549, 4)
    close = torch.cat([teacher[..., :4], torch.logit(teacher[..., 4:].clamp(1e-4, 1 - 1e-4))], -1)
    far = torch.randn(2, 3549, 85)
    assert distill_loss(close, teacher)["total"] < distill_loss(far, teacher)["total"]
    assert float(distill_loss(close, teacher)["box"]) == pytest.approx(0.0, abs=1e-6)


def test_found_people_are_matched_once() -> None:
    reference = [(0.0, 0.0, 10.0, 20.0), (50.0, 50.0, 10.0, 20.0)]
    assert match(reference, [(1.0, 1.0, 10.0, 20.0)]) == 1
    assert match(reference, [(1.0, 1.0, 10.0, 20.0), (1.0, 1.0, 10.0, 20.0)]) == 1
    assert match(reference, [(30.0, 30.0, 5.0, 5.0)]) == 0
    result = score([(2, 1, 1), (0, 1, 0), (1, 0, 0)])
    assert result["recall"] == 0.333 and result["precision"] == 0.5
    assert (
        result["pictures_with_people_found"] == 0.5 and result["pictures_without_people_found"] == 1
    )


def test_the_network_has_the_pis_output_layout() -> None:
    model = YOLOX("nano").eval()
    with torch.no_grad():
        out = model(torch.zeros(1, 3, 416, 416))
    assert out.shape == (1, 3549, 85)
    assert float(out[..., 4:].min()) >= 0 and float(out[..., 4:].max()) <= 1


@pytest.mark.skipif(not (MODELS / "yolox_nano.pth").is_file(), reason="run `pgtrain models` first")
def test_official_weights_give_the_pis_answers() -> None:
    import cv2

    from pgtrain.yolox_model import load

    model = load("nano", str(MODELS / "yolox_nano.pth"))
    x = (RNG.random((1, 3, 416, 416)) * 255).astype(np.float32)
    with torch.no_grad():
        ours = model(torch.from_numpy(x)).numpy()
    net = cv2.dnn.readNet(str(MODELS / "yolox_nano.onnx"))
    net.setInput(x)
    assert np.abs(ours - net.forward()).max() < 1e-3


# --- the local page --------------------------------------------------------------------------


async def test_the_page_needs_its_key_and_its_own_address(
    tmp_path: Path, aiohttp_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pgtrain import web

    monkeypatch.setattr(web.weights, "ensure_faces", lambda folder: folder)
    monkeypatch.setattr(web, "FaceTool", lambda folder: object())
    page = web.Page(tmp_path, 8790)
    client = await aiohttp_client(page.app())
    host = {"Host": "127.0.0.1:8790"}
    assert (await client.get("/api/people", headers=host)).status == 403
    assert (await client.get("/api/people", headers={**host, "X-Key": page.key})).status == 200
    rebinding = {"Host": "evil.example:8790", "X-Key": page.key}
    assert (await client.get("/api/people", headers=rebinding)).status == 403
    (tmp_path / "secret.jpg").write_bytes(b"x")
    thumb = await client.get("/api/thumb", params={"path": str(tmp_path / "secret.jpg")},
                             headers={**host, "X-Key": page.key})  # fmt: skip
    assert thumb.status == 403  # only inside a folder that was searched
    bad_name = await client.post("/api/people/..", headers={**host, "X-Key": page.key})
    assert bad_name.status in (400, 404)
