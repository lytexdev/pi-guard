from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from pi_guard.config.files import CameraConfig, FaceSettings, Zone
from pi_guard.core.events import Event, EventType
from pi_guard.detection import analysis
from pi_guard.detection.analysis import MotionAnalysis
from pi_guard.detection.faces import BiometricStore, Verdict, classify
from pi_guard.detection.motion import MotionDetector, MotionEnd, MotionParams, MotionStart
from pi_guard.detection.persons import PersonResult
from pi_guard.detection.zones import build_mask

from .helpers import fake_snapshot, make_app

cv2 = pytest.importorskip("cv2")
W, H = 320, 180
UTC_ZONE = ZoneInfo("UTC")


def cam(**motion: object) -> CameraConfig:
    return CameraConfig.model_validate(
        {
            "id": "cam",
            "name": "Cam",
            "device": "/dev/video0",
            "resolution": "1280x720",
            "motion": {"min_duration_seconds": 1.0, **motion},
        }
    )


def scene(rng: np.random.Generator, box_x: int | None = None, brightness: int = 90) -> np.ndarray:
    frame = np.full((H, W), brightness, dtype=np.uint8)
    frame = np.clip(frame + rng.normal(0, 3, (H, W)), 0, 255).astype(np.uint8)  # sensor noise
    if box_x is not None:
        frame[60:140, box_x : box_x + 40] = 230
    return frame


def run(detector: MotionDetector, frames: list[np.ndarray], fps: float = 4.0) -> None:
    for i, frame in enumerate(frames):
        detector.process(frame, i / fps)


def detector(changes: list[object], **motion: object) -> MotionDetector:
    return MotionDetector(cam(**motion), 50, changes.append, UTC_ZONE)


def test_noise_alone_is_not_motion() -> None:
    rng = np.random.default_rng(1)
    changes: list[object] = []
    run(detector(changes), [scene(rng) for _ in range(40)])
    assert changes == []


def test_moving_object_starts_and_ends_motion() -> None:
    rng = np.random.default_rng(2)
    changes: list[object] = []
    frames = [scene(rng) for _ in range(8)]
    frames += [scene(rng, box_x=20 + 12 * i) for i in range(12)]  # 3 s of movement
    frames += [scene(rng) for _ in range(20)]  # 5 s of calm
    run(detector(changes), frames)
    assert isinstance(changes[0], MotionStart) and changes[0].camera == "cam"
    assert isinstance(changes[-1], MotionEnd) and changes[-1].duration >= 2
    assert len(changes) == 2


def test_short_blip_below_min_duration_is_ignored() -> None:
    rng = np.random.default_rng(3)
    changes: list[object] = []
    frames = [scene(rng) for _ in range(8)] + [scene(rng, box_x=100)] * 2 + [scene(rng)] * 10
    run(detector(changes, min_duration_seconds=2.0), frames)
    assert changes == []


def test_light_switch_is_not_motion() -> None:
    rng = np.random.default_rng(4)
    changes: list[object] = []
    frames = [scene(rng) for _ in range(8)] + [scene(rng, brightness=200) for _ in range(20)]
    run(detector(changes), frames)
    assert changes == []


def test_exclusion_zone_hides_motion() -> None:
    rng = np.random.default_rng(5)
    changes: list[object] = []
    zone = {"name": "tree", "kind": "exclude", "points": [[0, 0], [1, 0], [1, 1], [0, 1]]}
    frames = [scene(rng) for _ in range(8)] + [scene(rng, box_x=20 + 12 * i) for i in range(12)]
    run(detector(changes, zones=[zone]), frames)
    assert changes == []


def test_schedule_outside_window_disables_detection() -> None:
    rng = np.random.default_rng(6)
    changes: list[object] = []
    noon = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    d = MotionDetector(
        cam(schedule=["22:00-06:00"]), 50, changes.append, UTC_ZONE, clock=lambda: noon
    )
    run(d, [scene(rng) for _ in range(8)] + [scene(rng, box_x=20 + 12 * i) for i in range(12)])
    assert changes == []


def test_sensitivity_mapping() -> None:
    low = MotionParams.from_camera(cam(), 0)
    high = MotionParams.from_camera(cam(), 100)
    assert low.pixel_threshold > high.pixel_threshold
    assert low.min_area_percent > high.min_area_percent
    override = MotionParams.from_camera(cam(sensitivity=100), 0)
    assert override == high


def test_zone_mask() -> None:
    include = Zone(name="left", points=[(0, 0), (0.5, 0), (0.5, 1), (0, 1)])
    exclude = Zone(name="corner", kind="exclude", points=[(0, 0), (0.25, 0), (0.25, 0.5)])
    mask = build_mask([include, exclude], 100, 100)
    assert mask[50, 10] == 255 and mask[50, 90] == 0
    assert mask[2, 20] == 0  # inside the excluded triangle
    assert build_mask([], 10, 10).min() == 255


# --- recognition ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("score", "verdict"),
    [
        (0.60, Verdict.KNOWN),
        (0.46, Verdict.KNOWN),
        (0.42, Verdict.UNCERTAIN),
        (0.35, Verdict.UNCERTAIN),
        (0.33, Verdict.UNKNOWN),
        (-0.2, Verdict.UNKNOWN),
    ],
)
def test_recognition_verdicts(score: float, verdict: Verdict) -> None:
    result = classify("Erna", score, FaceSettings(match_threshold=0.40, uncertain_margin=0.06))
    assert result.verdict is verdict
    assert (result.name == "Erna") == (verdict is Verdict.KNOWN)


def test_biometric_store_add_cap_and_delete(tmp_path: Path) -> None:
    store = BiometricStore(tmp_path / "bio.db")
    vector = np.ones(128, dtype=np.float32)
    for _ in range(4):
        count = store.add("Erna", vector, "telegram:1", max_samples=3)
    assert count == 3
    assert store.people()[0][:2] == ("Erna", 3)
    assert (tmp_path / "bio.db").stat().st_mode & 0o077 == 0
    assert store.delete("erna")  # case-insensitive
    assert store.people() == [] and store.samples() == []
    assert not store.delete("Erna")
    store.close()
    assert b"Erna" not in (tmp_path / "bio.db").read_bytes()


class FakePersons:
    """Returns the given results one per picture, the last one again when they run out."""

    def __init__(self, *results: PersonResult) -> None:
        self.results = list(results)
        self.calls = 0

    def detect(self, path: Path) -> PersonResult:
        self.calls += 1
        return self.results[min(self.calls, len(self.results)) - 1]


class FakeFaces:
    """One list of (verdict, clear) per picture, one entry per face; the last list repeats."""

    def __init__(self, *pictures: list[tuple[Verdict, bool]] | Verdict) -> None:
        self.pictures = [[(p, False)] if isinstance(p, Verdict) else p for p in pictures]
        self.calls = 0
        self.face = 0

    def identify(self, image, face):  # type: ignore[no-untyped-def]
        from pi_guard.detection.faces import Identification

        if self.face == 0:
            self.calls += 1
        picture = self.pictures[min(self.calls, len(self.pictures)) - 1]
        verdict, clear = picture[self.face]
        self.face = (self.face + 1) % len(picture)
        return Identification(verdict, "Erna" if verdict is Verdict.KNOWN else None, 0.7,
                              clear=clear)  # fmt: skip


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(analysis, "ATTEMPT_SPACING_SECONDS", 0.0)


async def _events(tmp_path: Path, persons, faces, moving: bool = False) -> list[Event]:  # type: ignore[no-untyped-def]
    """`moving`: the motion goes on, so further pictures are taken."""
    app = make_app(tmp_path, person_detection_enabled=True)
    fake_snapshot(app)
    if moving:
        app.motion["living_room"] = SimpleNamespace(active=True)  # type: ignore[assignment]
    events = app.bus.subscribe("t")
    analysis_ = MotionAnalysis(app, persons, faces)
    analysis_.on_change(MotionStart("living_room", 3.0))
    await asyncio.sleep(0.3)
    app.db.close()
    found = []
    while not events.empty():
        found.append(events.get_nowait())
    return found


async def _analyse(tmp_path: Path, persons, faces, moving: bool = False) -> list[EventType]:  # type: ignore[no-untyped-def]
    return [e.type for e in await _events(tmp_path, persons, faces, moving)]


IMAGE = np.zeros((10, 10, 3), dtype=np.uint8)
ONE_FACE = np.zeros((1, 15), dtype=np.float32)
TWO_FACES = np.zeros((2, 15), dtype=np.float32)
FACE = PersonResult(0, ONE_FACE, IMAGE)
NOBODY = PersonResult(0, None, IMAGE)
STRANGER, UNCLEAR = [(Verdict.UNKNOWN, True)], [(Verdict.UNKNOWN, False)]


async def test_motion_then_known_person(tmp_path: Path) -> None:
    types = await _analyse(tmp_path, FakePersons(FACE), FakeFaces(Verdict.KNOWN))
    assert types == [EventType.MOTION_DETECTED, EventType.KNOWN_PERSON]


async def test_clear_stranger_is_reported_at_once(tmp_path: Path) -> None:
    persons = FakePersons(FACE)
    events = await _events(tmp_path, persons, FakeFaces(STRANGER), moving=True)
    assert [e.type for e in events] == [EventType.MOTION_DETECTED, EventType.UNKNOWN_PERSON]
    assert persons.calls == 1 and events[1].data["pictures"] == 1


async def test_unclear_face_once_is_a_person_not_a_stranger(tmp_path: Path) -> None:
    """A family member seen badly once must not raise the stranger alarm."""
    events = await _events(tmp_path, FakePersons(FACE), FakeFaces(UNCLEAR))
    assert [e.type for e in events] == [EventType.MOTION_DETECTED, EventType.PERSON_DETECTED]
    assert events[1].data["recognition"] == "uncertain"


async def test_unclear_face_matching_nobody_twice_is_a_stranger(tmp_path: Path) -> None:
    persons = FakePersons(FACE)
    types = await _analyse(tmp_path, persons, FakeFaces(UNCLEAR), moving=True)
    assert types == [EventType.MOTION_DETECTED, EventType.UNKNOWN_PERSON]
    assert persons.calls == 3  # decided after the last picture


async def test_later_picture_recognises_the_person(tmp_path: Path) -> None:
    faces = FakeFaces(Verdict.UNCERTAIN, Verdict.KNOWN)
    events = await _events(tmp_path, FakePersons(FACE), faces, moving=True)
    assert [e.type for e in events] == [EventType.MOTION_DETECTED, EventType.KNOWN_PERSON]
    assert events[1].data["name"] == "Erna" and events[1].data["pictures"] == 2


async def test_stranger_next_to_family_member_is_a_stranger(tmp_path: Path) -> None:
    faces = FakeFaces([(Verdict.KNOWN, True), (Verdict.UNKNOWN, False)])
    events = await _events(tmp_path, FakePersons(PersonResult(0, TWO_FACES, IMAGE)), faces)
    assert [e.type for e in events] == [EventType.MOTION_DETECTED, EventType.UNKNOWN_PERSON]
    assert events[1].data["known"] == ["Erna"]


async def test_body_without_face_is_person_not_intruder(tmp_path: Path) -> None:
    persons = FakePersons(PersonResult(1, None, IMAGE))
    types = await _analyse(tmp_path, persons, FakeFaces(STRANGER), moving=True)
    assert types == [EventType.MOTION_DETECTED, EventType.PERSON_DETECTED]
    assert persons.calls == 3  # looked for a face in every picture


async def test_without_recognition_a_person_is_reported_at_once(tmp_path: Path) -> None:
    persons = FakePersons(PersonResult(1, None, IMAGE, detector="yolox", hog_bodies=0))
    events = await _events(tmp_path, persons, None, moving=True)
    assert [e.type for e in events] == [EventType.MOTION_DETECTED, EventType.PERSON_DETECTED]
    assert persons.calls == 1
    assert events[1].data == {"bodies": 1, "faces": 0, "detector": "yolox", "hog": 0,
                              "pictures": 1}  # fmt: skip


async def test_person_in_a_later_picture_is_found(tmp_path: Path) -> None:
    persons = FakePersons(NOBODY, PersonResult(1, None, IMAGE))
    types = await _analyse(tmp_path, persons, None, moving=True)
    assert types == [EventType.MOTION_DETECTED, EventType.PERSON_DETECTED]


async def test_nobody_in_any_picture_is_only_motion(tmp_path: Path) -> None:
    persons = FakePersons(NOBODY)
    types = await _analyse(tmp_path, persons, FakeFaces(STRANGER), moving=True)
    assert types == [EventType.MOTION_DETECTED] and persons.calls == 3


async def test_motion_only_without_person_detection(tmp_path: Path) -> None:
    assert await _analyse(tmp_path, None, None) == [EventType.MOTION_DETECTED]


async def test_known_person_does_not_suppress_motion_alert(tmp_path: Path) -> None:
    from pi_guard.core.modes import Mode

    app = make_app(tmp_path, person_detection_enabled=True)
    fake_snapshot(app)
    app.modes.set_manual(Mode.AWAY)
    analysis_ = MotionAnalysis(app, FakePersons(FACE), FakeFaces(Verdict.KNOWN))
    events = app.bus.subscribe("t")
    analysis_.on_change(MotionStart("living_room", 3.0))
    await asyncio.sleep(0.2)
    while not events.empty():
        await app.notifier.handle(events.get_nowait())
    while not app.notifier._queue.empty():
        await app.notifier._deliver(app.notifier._queue.get_nowait())
    texts = app.providers["telegram"].texts()
    assert any(t.startswith("Movement seen") for t in texts)
    app.db.close()
