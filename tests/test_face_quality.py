"""Recognition logic with synthetic embeddings: look-alikes, mixed uploads, poor faces."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from pi_guard.config.files import FaceSettings
from pi_guard.detection.faces import (
    BiometricStore,
    FaceRecognizer,
    Verdict,
    consistent,
    person_scores,
)

RNG = np.random.default_rng(7)


def unit(vector: np.ndarray) -> np.ndarray:
    return (vector / np.linalg.norm(vector)).astype(np.float32)


def identity() -> np.ndarray:
    return unit(RNG.normal(size=128))


def views(base: np.ndarray, count: int, noise: float = 0.6) -> list[np.ndarray]:
    """Pictures of one person: the identity plus per-picture variation (light, angle)."""
    return [unit(base + noise * unit(RNG.normal(size=128))) for _ in range(count)]


def blend(a: np.ndarray, b: np.ndarray, weight: float) -> np.ndarray:
    return unit((1 - weight) * a + weight * b)


@pytest.fixture
def recognizer(tmp_path: Path) -> Any:
    import cv2

    rec = object.__new__(FaceRecognizer)
    rec._cv2 = cv2
    rec.settings = FaceSettings()
    rec.store = BiometricStore(tmp_path / "bio.db")
    rec._cache = None
    probe: dict[str, np.ndarray] = {}
    rec.problem = lambda image, face, enrolling: (None, image)  # type: ignore[method-assign]
    rec._embed = lambda aligned: probe["value"]  # type: ignore[method-assign]
    rec.probe = probe
    yield rec
    rec.store.close()


def look(rec: Any, embedding: np.ndarray) -> Any:
    rec.probe["value"] = embedding
    return rec.identify(np.zeros((4, 4, 3), np.uint8), np.zeros(15, np.float32))


def test_family_is_recognised_and_strangers_are_not(recognizer: Any) -> None:
    family = {name: identity() for name in ("Lena", "Mama", "Oma", "Ben")}
    for name, base in family.items():
        report = recognizer.enroll_many(name, views(base, 15), "test")
        assert report.stored >= 12 and not report.conflicts
    for name, base in family.items():
        for picture in views(base, 5):
            result = look(recognizer, picture)
            assert result.verdict is Verdict.KNOWN and result.name == name
    for _ in range(50):
        assert look(recognizer, identity()).verdict is Verdict.UNKNOWN


def test_look_alikes_are_not_mixed_up(recognizer: Any) -> None:
    mama = identity()
    oma = blend(mama, identity(), 0.45)  # strongly related faces
    recognizer.enroll_many("Mama", views(mama, 15, 0.4), "test")
    recognizer.enroll_many("Oma", views(oma, 15, 0.4), "test")
    for picture in views(mama, 10, 0.4):
        result = look(recognizer, picture)
        assert result.name in ("Mama", None)  # never Oma
    # Somebody exactly between the two is not claimed to be either.
    middle = look(recognizer, blend(mama, oma, 0.5))
    assert middle.verdict is not Verdict.KNOWN
    assert middle.runner_up in ("Mama", "Oma")


def test_pictures_of_another_enrolled_person_are_refused(recognizer: Any) -> None:
    oma, mama = identity(), identity()
    recognizer.enroll_many("Oma", views(oma, 12), "test")
    # Uploaded to Mama by mistake: Oma's pictures.
    report = recognizer.enroll_many("Mama", views(oma, 6), "test")
    assert report.stored == 0 and report.conflicts == {"Oma": 6}
    assert recognizer.enroll_many("Mama", views(mama, 8), "test").stored == 8


def test_somebody_else_in_a_video_is_left_out() -> None:
    ben = identity()
    samples = views(ben, 10) + views(identity(), 2)
    kept, outliers = consistent(samples)
    assert outliers == 2 and len(kept) == 10


def test_scores_use_several_samples() -> None:
    base = identity()
    probe = views(base, 1)[0]
    samples = [("A", s) for s in views(base, 6)] + [("B", probe)]  # one lucky sample
    scores = person_scores(probe, samples)
    assert scores["B"] == pytest.approx(1.0)
    assert set(scores) == {"A", "B"}


def test_poor_faces_are_never_known(tmp_path: Path) -> None:
    import cv2

    rec = object.__new__(FaceRecognizer)
    rec._cv2 = cv2

    class Aligner:
        def alignCrop(self, image: Any, face: Any) -> Any:
            return image[:112, :112]

    rec._sface = Aligner()
    sharp = (RNG.random((200, 200, 3)) * 255).astype(np.uint8)
    flat = np.full((200, 200, 3), 128, np.uint8)

    def face(size: float, nose_offset: float = 0.0, score: float = 0.95) -> np.ndarray:
        row = np.zeros(15, np.float32)
        row[:4] = (10, 10, size, size)
        row[4:10] = (40, 50, 80, 50, 60 + nose_offset * 40, 70)  # eyes 40 px apart
        row[14] = score
        return row

    assert rec.problem(sharp, face(100), enrolling=True)[0] is None
    assert rec.problem(sharp, face(40), enrolling=True)[0] == "faces.too_small"
    assert rec.problem(sharp, face(40), enrolling=False)[0] is None
    assert rec.problem(sharp, face(100, nose_offset=0.45), enrolling=True)[0] == "faces.turned"
    assert rec.problem(flat, face(100), enrolling=True)[0] == "faces.blurry"
    assert rec.problem(sharp, face(100, score=0.6), enrolling=True)[0] == "faces.unclear"
