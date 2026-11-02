"""Optional recognition of enrolled, consenting people.

Only 128-number face embeddings (SFace) are stored, in a separate SQLite file
readable by the service user alone. Enrollment photos are never kept. Nothing
here is ever sent to Telegram, Signal or MQTT; only a name and a score leave
this module.

Results are deliberately three-valued: a match must clear the threshold by a
margin, a near miss is reported as "uncertain", everything else is "unknown".
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from pi_guard.config.files import FaceSettings
from pi_guard.core.timeutil import utcnow
from pi_guard.detection.models import SFACE, YUNET
from pi_guard.detection.persons import Image, PersonDetector
from pi_guard.security.files import secure_file

log = logging.getLogger(__name__)

Embedding = npt.NDArray[np.float32]


class EnrollmentError(Exception):
    """`key` is a translation key explaining the problem."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


class Verdict(StrEnum):
    KNOWN = "known"
    UNCERTAIN = "uncertain"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Identification:
    verdict: Verdict
    name: str | None
    score: float


class BiometricStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        secure_file(path)
        self._conn.execute("PRAGMA secure_delete=ON")  # overwrite deleted rows with zeros
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS people (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, enrolled_at TEXT NOT NULL,
                enrolled_by TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS samples (
                id INTEGER PRIMARY KEY,
                person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
                embedding BLOB NOT NULL, created_at TEXT NOT NULL);
            PRAGMA foreign_keys=ON;
        """)

    def add(self, name: str, embedding: Embedding, actor: str, max_samples: int) -> int:
        now = utcnow().isoformat()
        self._conn.execute(
            "INSERT INTO people (name, enrolled_at, enrolled_by) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO NOTHING",
            (name, now, actor),
        )
        person_id = self._conn.execute("SELECT id FROM people WHERE name = ?", (name,)).fetchone()[
            0
        ]
        self._conn.execute(
            "INSERT INTO samples (person_id, embedding, created_at) VALUES (?, ?, ?)",
            (person_id, embedding.astype(np.float32).tobytes(), now),
        )
        self._conn.execute(
            "DELETE FROM samples WHERE person_id = ? AND id NOT IN "
            "(SELECT id FROM samples WHERE person_id = ? ORDER BY id DESC LIMIT ?)",
            (person_id, person_id, max_samples),
        )
        count: int = self._conn.execute(
            "SELECT COUNT(*) FROM samples WHERE person_id = ?", (person_id,)
        ).fetchone()[0]
        return count

    def people(self) -> list[tuple[str, int, str]]:
        return [
            tuple(r)
            for r in self._conn.execute(
                "SELECT p.name, COUNT(s.id), p.enrolled_at FROM people p "
                "LEFT JOIN samples s ON s.person_id = p.id GROUP BY p.id ORDER BY p.name"
            )
        ]

    def samples(self) -> list[tuple[str, Embedding]]:
        rows = self._conn.execute(
            "SELECT p.name, s.embedding FROM samples s JOIN people p ON p.id = s.person_id"
        )
        return [(name, np.frombuffer(blob, dtype=np.float32)) for name, blob in rows]

    def delete(self, name: str) -> bool:
        deleted = (
            self._conn.execute(
                "DELETE FROM people WHERE name = ? COLLATE NOCASE", (name.strip(),)
            ).rowcount
            > 0
        )
        if deleted:
            self._conn.execute("VACUUM")
        return deleted

    def rename(self, old: str, new: str) -> bool:
        try:
            changed = self._conn.execute(
                "UPDATE people SET name = ? WHERE name = ? COLLATE NOCASE", (new, old)
            ).rowcount
        except sqlite3.IntegrityError:
            return False  # the new name is taken
        return changed > 0

    def close(self) -> None:
        self._conn.close()


class FaceRecognizer:
    def __init__(
        self,
        settings: FaceSettings,
        models_dir: Path,
        store: BiometricStore,
        detector: PersonDetector,
    ) -> None:
        import cv2

        if not (YUNET.present(models_dir) and SFACE.present(models_dir)):
            raise FileNotFoundError("face models missing; run `pi-guard models download`")
        self.settings = settings
        self.store = store
        self._detector = detector
        self._sface: Any = cv2.FaceRecognizerSF.create(str(SFACE.path(models_dir)), "")
        self._cache: list[tuple[str, Embedding]] | None = None

    def embedding(self, image: Image, face: npt.NDArray[np.float32]) -> Embedding:
        aligned = self._sface.alignCrop(image, face)
        feature: Embedding = self._sface.feature(aligned).ravel().astype(np.float32)
        return feature / (np.linalg.norm(feature) or 1.0)

    def prepare(self, photo: Path) -> Embedding:
        """Embedding of the single face in `photo`; the photo itself is not kept."""
        image = self._detector.load(photo)
        faces = self._detector.faces(image)
        if faces is None or len(faces) == 0:
            raise EnrollmentError("faces.no_face")
        if len(faces) > 1:
            raise EnrollmentError("faces.several_faces")
        return self.embedding(image, faces[0])

    def enroll(self, name: str, embedding: Embedding, actor: str) -> int:
        name = name.strip()
        if not name or len(name) > 64:
            raise EnrollmentError("faces.bad_name")
        count = self.store.add(name, embedding, actor, self.settings.max_samples_per_person)
        self._cache = None
        log.info("face sample stored for an enrolled person (%d samples)", count)
        return count

    def delete(self, name: str) -> bool:
        self._cache = None
        return self.store.delete(name)

    def identify(self, image: Image, face: npt.NDArray[np.float32]) -> Identification:
        if self._cache is None:
            self._cache = self.store.samples()
        if not self._cache:
            return Identification(Verdict.UNKNOWN, None, 0.0)
        probe = self.embedding(image, face)
        best_name, best = None, -1.0
        for name, sample in self._cache:
            score = float(np.dot(probe, sample / (np.linalg.norm(sample) or 1.0)))
            if score > best:
                best_name, best = name, score
        return classify(best_name, best, self.settings)


def classify(name: str | None, score: float, settings: FaceSettings) -> Identification:
    if score >= settings.match_threshold + settings.uncertain_margin:
        return Identification(Verdict.KNOWN, name, score)
    if score >= settings.match_threshold - settings.uncertain_margin:
        return Identification(Verdict.UNCERTAIN, None, score)
    return Identification(Verdict.UNKNOWN, None, score)
