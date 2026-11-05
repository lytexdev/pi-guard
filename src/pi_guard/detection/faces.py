"""Optional recognition of enrolled, consenting people.

Only 128-number face embeddings (SFace) are stored, in a separate SQLite file
readable by the service user alone. Enrollment photos are never kept. Nothing
here is ever sent to Telegram, Signal or MQTT; only a name and a score leave
this module.

Results are deliberately three-valued: a match must clear the threshold by a
margin, a near miss is reported as "uncertain", everything else is "unknown".

How mistakes are kept out (family members often look alike):
- enrollment takes only clear faces: big enough, sharp, both eyes visible, one per picture
- an upload is checked for consistency; pictures of somebody else in a video are dropped
- a sample that looks more like another enrolled person than like this one is refused
- a person's score is the mean of their best few samples, not one lucky sample
- "known" also needs a clear lead over the next most similar person
- faces too small or blurry to judge are "uncertain", never "known"
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from pi_guard.config.files import FaceSettings
from pi_guard.core.timeutil import utcnow
from pi_guard.detection.models import SFACE, YUNET
from pi_guard.detection.persons import FULL_WIDTH, Image, PersonDetector
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
    runner_up: str | None = None  # the next most similar person, for the test page
    runner_up_score: float = 0.0
    reason: str | None = None  # why a face could not be judged


# Quality limits: (enrollment, recognition). Sizes in pixels of the full picture.
MIN_FACE_PX = (64, 36)
MIN_DETECTION_SCORE = (0.88, 0.0)
# Horizontal offset of the nose from the middle between the eyes, relative to the eye
# distance: 0 looks straight at the camera, ~0.5 is a half profile.
MAX_TURN = (0.42, 0.55)
FRONT_TURN = 0.12  # up to here a face counts as looking at the camera
MIN_SHARPNESS = (25.0, 8.0)  # variance of the Laplacian on the aligned 112x112 face
TOP_K = 3
PERSON_MARGIN = 0.06  # lead over the next most similar person needed for "known"
SAME_PERSON_MIN = 0.30  # samples of one upload must be at least this similar to each other
DOMINANT_FACE = 2.5  # a face this many times bigger than the others is the subject


# How well a person is learned: an estimate from what was stored, for advice to the user.
GOOD_SAMPLES = 15
GOOD_VIEWS = {"front": 6, "left": 3, "right": 3}
GOOD_LIGHT_SPREAD = 50.0  # between the darkest and brightest picture, of 255
GOOD_MATCH, POOR_MATCH = 0.55, 0.30  # similarity of a sample to the person's average
LOOK_ALIKE_LIMIT = 70
WEAK_BELOW, GOOD_FROM = 40, 75


class FaceSample(np.ndarray):
    """An embedding that remembers how the picture looked; the picture itself is not kept."""

    pose: str | None = None
    light: float | None = None


def with_context(embedding: Embedding, pose: str, light: float) -> FaceSample:
    sample = embedding.view(FaceSample)
    sample.pose, sample.light = pose, light
    return sample


def pose_of(face: npt.NDArray[np.float32]) -> str:
    """Which way a face looks in the picture, from the nose between the eyes."""
    right_eye, left_eye, nose = face[4:6], face[6:8], face[8:10]
    eye_distance = float(np.linalg.norm(left_eye - right_eye)) or 1.0
    offset = (float(nose[0]) - float(right_eye[0] + left_eye[0]) / 2) / eye_distance
    if abs(offset) <= FRONT_TURN:
        return "front"
    return "right" if offset > 0 else "left"


def enrollment_quality(rows: list[dict[str, Any]], look_alike: str | None) -> dict[str, Any]:
    """Score from 0 to 100 and what would help most. Samples from before poses were recorded
    were all taken roughly from the front, so they count as such."""
    count = len(rows)
    views = {"front": 0, "left": 0, "right": 0}
    for row in rows:
        views[row.get("pose") or "front"] += 1
    lights = [r["light"] for r in rows if r.get("light") is not None]
    spread = max(lights) - min(lights) if lights else 0.0
    match = float(np.mean([r["match"] for r in rows])) if rows else 0.0
    agreement = min(1.0, max(0.0, (match - POOR_MATCH) / (GOOD_MATCH - POOR_MATCH)))
    score = (
        40 * min(count, GOOD_SAMPLES) / GOOD_SAMPLES
        + 20 * min(views["front"], GOOD_VIEWS["front"]) / GOOD_VIEWS["front"]
        + 10 * min(views["left"], GOOD_VIEWS["left"]) / GOOD_VIEWS["left"]
        + 10 * min(views["right"], GOOD_VIEWS["right"]) / GOOD_VIEWS["right"]
        + 10 * min(spread, GOOD_LIGHT_SPREAD) / GOOD_LIGHT_SPREAD
        + 10 * agreement
    )
    tips: list[dict[str, Any]] = []
    if look_alike:
        score = min(score, LOOK_ALIKE_LIMIT)
        tips.append({"key": "faces.tip_similar", "other": look_alike})
    if count and agreement < 0.5:
        tips.append({"key": "faces.tip_mixed"})
    for view, wanted in GOOD_VIEWS.items():
        if views[view] < wanted:
            tips.append({"key": f"faces.tip_{view}", "count": wanted - views[view]})
    if count < GOOD_SAMPLES:
        tips.append({"key": "faces.tip_more", "count": GOOD_SAMPLES - count})
    if spread < GOOD_LIGHT_SPREAD:
        tips.append({"key": "faces.tip_light"})
    points = round(score)
    level = "weak" if points < WEAK_BELOW else "good" if points >= GOOD_FROM else "ok"
    return {"score": points, "level": level, "views": views, "tips": tips[:3]}


@dataclass
class EnrollReport:
    stored: int = 0
    total: int = 0
    outliers: int = 0
    conflicts: dict[str, int] = field(default_factory=dict)


def person_scores(probe: Embedding, samples: list[tuple[str, Embedding]]) -> dict[str, float]:
    """Per person: mean of the TOP_K best sample similarities (fewer if fewer samples)."""
    by_person: dict[str, list[float]] = {}
    for name, sample in samples:
        by_person.setdefault(name, []).append(float(np.dot(probe, sample)))
    return {
        name: float(np.mean(sorted(values, reverse=True)[:TOP_K]))
        for name, values in by_person.items()
    }


def consistent(samples: list[Embedding]) -> tuple[list[Embedding], int]:
    """Keep the samples that show the same person as most of the upload."""
    if len(samples) < 3:
        return samples, 0
    matrix = np.stack(samples) @ np.stack(samples).T
    np.fill_diagonal(matrix, np.nan)
    medians = np.nanmedian(matrix, axis=1)
    anchor = int(np.argmax(medians))
    keep = [
        sample
        for index, sample in enumerate(samples)
        if index == anchor
        or matrix[anchor, index] >= SAME_PERSON_MIN
        or medians[index] >= SAME_PERSON_MIN
    ]
    return keep, len(samples) - len(keep)


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
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(samples)")}
        for column, kind in (("pose", "TEXT"), ("light", "REAL")):
            if column not in columns:
                self._conn.execute(f"ALTER TABLE samples ADD COLUMN {column} {kind}")
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(people)")}
        for column, kind in (("profile", "TEXT"), ("picture", "BLOB")):
            if column not in columns:
                self._conn.execute(f"ALTER TABLE people ADD COLUMN {column} {kind}")

    def profile(self, name: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT profile, picture IS NOT NULL FROM people WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        return {**json.loads(row[0] or "{}"), "picture": bool(row[1])} if row else {}

    def set_profile(self, name: str, values: dict[str, str]) -> bool:
        cursor = self._conn.execute(
            "UPDATE people SET profile = ? WHERE name = ? COLLATE NOCASE",
            (json.dumps(values), name),
        )
        return cursor.rowcount > 0

    def picture(self, name: str) -> bytes | None:
        row = self._conn.execute(
            "SELECT picture FROM people WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        return bytes(row[0]) if row and row[0] else None

    def set_picture(self, name: str, jpeg: bytes | None) -> bool:
        cursor = self._conn.execute(
            "UPDATE people SET picture = ? WHERE name = ? COLLATE NOCASE", (jpeg, name)
        )
        return cursor.rowcount > 0

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
            "INSERT INTO samples (person_id, embedding, created_at, pose, light) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                person_id,
                np.asarray(embedding, dtype=np.float32).tobytes(),
                now,
                getattr(embedding, "pose", None),
                getattr(embedding, "light", None),
            ),
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

    def details(self, name: str) -> list[dict[str, Any]]:
        """One row per stored sample; pictures are not kept, only these numbers."""
        rows = self._conn.execute(
            "SELECT s.id, s.created_at, s.embedding, p.enrolled_by, s.pose, s.light FROM samples s "
            "JOIN people p ON p.id = s.person_id WHERE p.name = ? COLLATE NOCASE ORDER BY s.id",
            (name,),
        ).fetchall()
        vectors = [np.frombuffer(r[2], dtype=np.float32) for r in rows]
        vectors = [v / (np.linalg.norm(v) or 1.0) for v in vectors]
        mean = np.mean(vectors, axis=0) if vectors else np.zeros(1, dtype=np.float32)
        return [
            {"id": r[0], "created": r[1], "by": r[3], "bytes": len(r[2]), "numbers": len(v),
             "match": round(float(np.dot(v, mean / (np.linalg.norm(mean) or 1.0))), 2),
             "pose": r[4], "light": r[5]}
            for r, v in zip(rows, vectors, strict=True)
        ]  # fmt: skip

    def delete_sample(self, sample_id: int) -> bool:
        return self._conn.execute("DELETE FROM samples WHERE id = ?", (sample_id,)).rowcount > 0

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
        self._cv2 = cv2
        self.settings = settings
        self.store = store
        self._detector = detector
        self._sface: Any = cv2.FaceRecognizerSF.create(str(SFACE.path(models_dir)), "")
        self._cache: list[tuple[str, Embedding]] | None = None

    # --- faces --------------------------------------------------------------------------

    def _aligned(self, image: Image, face: npt.NDArray[np.float32]) -> Image:
        return self._sface.alignCrop(image, face)  # type: ignore[no-any-return]

    def _embed(self, aligned: Image) -> Embedding:
        feature: Embedding = self._sface.feature(aligned).ravel().astype(np.float32)
        return feature / (np.linalg.norm(feature) or 1.0)

    def embedding(self, image: Image, face: npt.NDArray[np.float32]) -> Embedding:
        return self._embed(self._aligned(image, face))

    def problem(
        self, image: Image, face: npt.NDArray[np.float32], enrolling: bool
    ) -> tuple[str | None, Image]:
        """(translation key of what is wrong with this face or None, aligned face)."""
        level = 0 if enrolling else 1
        w, h = float(face[2]), float(face[3])
        if min(w, h) < MIN_FACE_PX[level]:
            return "faces.too_small", image
        if float(face[14]) < MIN_DETECTION_SCORE[level]:
            return "faces.unclear", image
        right_eye, left_eye, nose = face[4:6], face[6:8], face[8:10]
        eye_distance = float(np.linalg.norm(left_eye - right_eye)) or 1.0
        turn = abs(float(nose[0]) - float(right_eye[0] + left_eye[0]) / 2) / eye_distance
        if turn > MAX_TURN[level]:
            return "faces.turned", image
        aligned = self._aligned(image, face)
        gray = self._cv2.cvtColor(aligned, self._cv2.COLOR_BGR2GRAY)
        if float(self._cv2.Laplacian(gray, self._cv2.CV_64F).var()) < MIN_SHARPNESS[level]:
            return "faces.blurry", aligned
        return None, aligned

    def prepare(self, photo: Path) -> FaceSample:
        """Embedding of the person in `photo` (one clear face); the photo is not kept."""
        image = self._detector.load(photo, FULL_WIDTH)
        faces = self._detector.faces_full(image, 1280)
        if faces is None or len(faces) == 0:
            raise EnrollmentError("faces.no_face")
        if len(faces) > 1:
            areas = sorted((float(f[2] * f[3]) for f in faces), reverse=True)
            if areas[0] < DOMINANT_FACE * areas[1]:
                raise EnrollmentError("faces.several_faces")
        face = max(faces, key=lambda f: float(f[2] * f[3]))
        problem, aligned = self.problem(image, face, enrolling=True)
        if problem:
            raise EnrollmentError(problem)
        light = float(self._cv2.cvtColor(aligned, self._cv2.COLOR_BGR2GRAY).mean())
        return with_context(self._embed(aligned), pose_of(face), light)

    # --- enrollment ---------------------------------------------------------------------

    def _samples(self) -> list[tuple[str, Embedding]]:
        if self._cache is None:
            self._cache = [
                (name, sample / (np.linalg.norm(sample) or 1.0))
                for name, sample in self.store.samples()
            ]
        return self._cache

    def enroll_many(self, name: str, samples: list[Embedding], actor: str) -> EnrollReport:
        """Store an upload's samples after the consistency and look-alike checks."""
        name = name.strip()
        if not name or len(name) > 64:
            raise EnrollmentError("faces.bad_name")
        report = EnrollReport()
        kept, report.outliers = consistent(samples)
        existing = self._samples()
        own = [(n, s) for n, s in existing if n.casefold() == name.casefold()]
        others = [(n, s) for n, s in existing if n.casefold() != name.casefold()]
        # Relatives may look alike, so "similar to somebody else" alone is no reason to refuse:
        # a picture is refused when it matches another person about as well as it matches
        # this person (their stored samples plus the rest of this upload).
        known_at = self.settings.match_threshold + self.settings.uncertain_margin
        for index, sample in enumerate(kept):
            other_scores = person_scores(sample, others)
            if other_scores:
                other, score = max(other_scores.items(), key=lambda item: item[1])
                pool = [s for _, s in own] + [s for i, s in enumerate(kept) if i != index]
                mine = person_scores(sample, [("self", s) for s in pool]).get("self", -1.0)
                if score >= known_at and score >= mine - PERSON_MARGIN:
                    report.conflicts[other] = report.conflicts.get(other, 0) + 1
                    continue
            report.total = self.store.add(name, sample, actor, self.settings.max_samples_per_person)
            report.stored += 1
            own.append((name, sample))
        self._cache = None
        if report.stored:
            log.info("face samples stored for an enrolled person (%d new, %d total)",
                     report.stored, report.total)  # fmt: skip
        return report

    def enroll(self, name: str, embedding: Embedding, actor: str) -> int:
        report = self.enroll_many(name, [embedding], actor)
        if report.conflicts:
            raise EnrollmentError("faces.looks_like_other")
        return report.total

    def delete(self, name: str) -> bool:
        self._cache = None
        return self.store.delete(name)

    def forget_cache(self) -> None:
        self._cache = None

    def quality(
        self, name: str, similar: dict[str, tuple[str, float]] | None = None
    ) -> dict[str, Any]:
        """How well this person is learned; see `enrollment_quality`."""
        known = self.similar_people() if similar is None else similar
        other, score = known.get(name, (None, 0.0))
        warn_at = self.settings.match_threshold - self.settings.uncertain_margin
        return enrollment_quality(self.store.details(name), other if score >= warn_at else None)

    def similar_people(self) -> dict[str, tuple[str, float]]:
        """For each person, the most similar other person and how similar (for warnings)."""
        samples = self._samples()
        names = sorted({n for n, _ in samples})
        result: dict[str, tuple[str, float]] = {}
        for name in names:
            mine = [s for n, s in samples if n == name]
            others = [(n, s) for n, s in samples if n != name]
            if not others:
                continue
            best: dict[str, list[float]] = {}
            for sample in mine:
                for other, score in person_scores(sample, others).items():
                    best.setdefault(other, []).append(score)
            other, scores = max(best.items(), key=lambda item: float(np.mean(item[1])))
            result[name] = (other, float(np.mean(scores)))
        return result

    # --- recognition --------------------------------------------------------------------

    def identify(self, image: Image, face: npt.NDArray[np.float32]) -> Identification:
        samples = self._samples()
        if not samples:
            return Identification(Verdict.UNKNOWN, None, 0.0)
        problem, aligned = self.problem(image, face, enrolling=False)
        if problem:
            # Too small or blurry to tell: never claim it is a family member.
            return Identification(Verdict.UNCERTAIN, None, 0.0, reason=problem)
        scores = person_scores(self._embed(aligned), samples)
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        best_name, best = ranked[0]
        runner_up, second = ranked[1] if len(ranked) > 1 else (None, 0.0)
        result = classify(best_name, best, self.settings)
        if result.verdict is Verdict.KNOWN and runner_up and best - second < PERSON_MARGIN:
            result = Identification(Verdict.UNCERTAIN, None, best, reason="faces.look_alike")
        return Identification(result.verdict, result.name, best, runner_up, second, result.reason)


def classify(name: str | None, score: float, settings: FaceSettings) -> Identification:
    if score >= settings.match_threshold + settings.uncertain_margin:
        return Identification(Verdict.KNOWN, name, score)
    if score >= settings.match_threshold - settings.uncertain_margin:
        return Identification(Verdict.UNCERTAIN, None, score)
    return Identification(Verdict.UNKNOWN, None, score)
