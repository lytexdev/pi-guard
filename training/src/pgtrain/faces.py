"""Faces on the computer, judged exactly as the Pi judges them.

pi-guard's own detector and recognizer run here with the same ONNX models, so a score on
this computer is the score the Pi would compute for the same picture. SFace itself is not
retrained: that needs many thousands of people. What these photos tune is the decision,
the threshold and margin in FaceSettings, see `calibrate`.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from pi_guard.config.files import FaceSettings
from pi_guard.detection.faces import (
    BiometricStore,
    FaceRecognizer,
    classify,
    person_scores,
)
from pi_guard.detection.persons import FULL_WIDTH, PersonDetector

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
# FaceSettings allows these; a recommendation stays inside them.
THRESHOLD_RANGE = (0.2, 0.8)
TARGET_FALSE_ACCEPT = 0.005  # at most one stranger in 200 taken for a family member


def images(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES)


@dataclass
class Found:
    box: tuple[int, int, int, int]
    embedding: np.ndarray | None  # None when the face is too poor to judge
    problem: str | None


class FaceTool:
    def __init__(self, models_dir: Path, settings: FaceSettings | None = None) -> None:
        self.settings = settings or FaceSettings()
        self.persons = PersonDetector(models_dir)
        self._workdir = tempfile.TemporaryDirectory(prefix="pgtrain-")
        store = BiometricStore(Path(self._workdir.name) / "unused.db")  # nothing is stored
        self.rec = FaceRecognizer(self.settings, models_dir, store, self.persons)

    def faces(self, path: Path) -> list[Found]:
        image = self.persons.load(path, FULL_WIDTH)
        rows = self.persons.faces_full(image, 1280)
        found = []
        for face in [] if rows is None else rows:
            box = tuple(round(float(v)) for v in face[:4])
            problem, _ = self.rec.problem(image, face, enrolling=False)
            embedding = None if problem else self.rec.embedding(image, face)
            found.append(Found(box, embedding, problem))  # type: ignore[arg-type]
        return found

    def main_face(self, path: Path) -> Found | None:
        """The biggest face of a photo, for photos that show one person."""
        found = self.faces(path)
        return max(found, key=lambda f: f.box[2] * f.box[3]) if found else None

    def people(self, folder: Path) -> dict[str, list[np.ndarray]]:
        """folder/<name>/*.jpg -> embeddings per name; photos without a usable face are left out."""
        result: dict[str, list[np.ndarray]] = {}
        for person in sorted(p for p in folder.iterdir() if p.is_dir()):
            for photo in images(person):
                face = self.main_face(photo)
                if face is not None and face.embedding is not None:
                    result.setdefault(person.name, []).append(face.embedding)
        return result

    def verdict(self, score: float) -> str:
        return classify("x", score, self.settings).verdict.value


def samples_of(people: dict[str, list[np.ndarray]]) -> list[tuple[str, np.ndarray]]:
    return [(name, e) for name, embeddings in people.items() for e in embeddings]


def who(probe: np.ndarray, people: dict[str, list[np.ndarray]]) -> list[tuple[str, float]]:
    """People ranked by how the Pi would score this face against them."""
    scores = person_scores(probe, samples_of(people))
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def leave_one_out(people: dict[str, list[np.ndarray]]) -> tuple[list[float], list[float]]:
    """Each photo against everybody's other photos, as the Pi scores a face.

    genuine: the score for the photo's own person; impostor: the best score among the others.
    """
    genuine: list[float] = []
    impostor: list[float] = []
    for name, embeddings in people.items():
        for index, probe in enumerate(embeddings):
            own = [(name, e) for i, e in enumerate(embeddings) if i != index]
            if own:
                genuine.append(person_scores(probe, own)[name])
            others = [(n, e) for n, es in people.items() if n != name for e in es]
            if others:
                impostor.append(max(person_scores(probe, others).values()))
    return genuine, impostor


def rates(
    genuine: list[float], impostor: list[float], threshold: float, margin: float
) -> dict[str, Any]:
    """How the Pi's three verdicts would come out with these settings."""
    known_at, unknown_below = threshold + margin, threshold - margin

    def share(values: list[float], test: Any) -> float | None:
        return round(sum(1 for v in values if test(v)) / len(values), 4) if values else None

    return {
        "threshold": round(threshold, 2), "margin": round(margin, 2),
        "family_known": share(genuine, lambda v: v >= known_at),
        "family_uncertain": share(genuine, lambda v: unknown_below <= v < known_at),
        "family_unknown": share(genuine, lambda v: v < unknown_below),
        "stranger_taken_for_family": share(impostor, lambda v: v >= known_at),
        "pairs": {"family": len(genuine), "stranger": len(impostor)},
    }  # fmt: skip


def recommend(genuine: list[float], impostor: list[float], margin: float) -> float | None:
    """The lowest threshold at which strangers are taken for family at most
    TARGET_FALSE_ACCEPT of the time. Lower thresholds recognise family more often."""
    if not impostor:
        return None
    ordered = sorted(impostor)
    index = min(len(ordered) - 1, int(np.ceil((1 - TARGET_FALSE_ACCEPT) * len(ordered))) - 1)
    known_at = ordered[index] + 0.01
    low, high = THRESHOLD_RANGE
    return round(min(high, max(low, known_at - margin)), 2)


def from_histogram(counts: Iterable[int], bins: int) -> Iterator[float]:
    """Scores back from a report histogram, one value per count at the middle of its bin."""
    for index, count in enumerate(counts):
        yield from [(index + 0.5) / bins] * int(count)
