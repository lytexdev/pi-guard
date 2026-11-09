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
    enrollment_quality,
    person_scores,
    pose_of,
    with_context,
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
    rec._cache = rec._rejected = None
    probe: dict[str, np.ndarray] = {}
    rec.problem = lambda image, face, enrolling: (None, image)  # type: ignore[method-assign]
    rec.learn_problem = lambda image, face: (None, image)  # type: ignore[method-assign]
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


def camera(rec: Any, embedding: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """A camera picture whose one face shows `embedding`."""
    rec.probe["value"] = embedding
    return np.zeros((4, 4, 3), np.uint8), np.zeros(15, np.float32)


def test_a_face_from_the_camera_is_learned(recognizer: Any) -> None:
    lena = identity()
    recognizer.enroll_many("Lena", views(lena, 10), "test")
    total, score = recognizer.learn("lena", *camera(recognizer, views(lena, 1)[0]), "web:admin")
    assert total == 11 and 0.2 < score < 1
    assert recognizer.store.details("Lena")[-1]["source"] == "camera"
    assert recognizer.store.details("Lena")[0]["source"] is None  # uploads stay unmarked


def test_only_enrolled_people_and_fitting_faces_are_learned(recognizer: Any) -> None:
    from pi_guard.detection.faces import EnrollmentError

    lena, oma = identity(), identity()
    recognizer.enroll_many("Lena", views(lena, 10), "test")
    recognizer.enroll_many("Oma", views(oma, 10), "test")
    with pytest.raises(EnrollmentError, match=r"faces\.learn_not_enrolled"):
        recognizer.learn("Fremder", *camera(recognizer, identity()), "web:admin")
    with pytest.raises(EnrollmentError, match=r"faces\.learn_too_different"):
        recognizer.learn("Lena", *camera(recognizer, identity()), "web:admin")  # a wrong click
    with pytest.raises(EnrollmentError, match=r"faces\.looks_like_other"):
        recognizer.learn("Lena", *camera(recognizer, blend(views(oma, 1)[0], lena, 0.2)),
                         "web:admin")  # fmt: skip
    recognizer.learn_problem = lambda image, face: ("faces.learn_too_small", image)
    with pytest.raises(EnrollmentError, match=r"faces\.learn_too_small"):
        recognizer.learn("Lena", *camera(recognizer, views(lena, 1)[0]), "web:admin")
    assert [c for _, c, _ in recognizer.store.people()] == [10, 10]


def test_a_face_that_is_not_somebody_stops_being_them(recognizer: Any) -> None:
    mama = identity()
    recognizer.enroll_many("Mama", views(mama, 15, 0.4), "test")
    neighbour = blend(mama, identity(), 0.5)  # taken for Mama, less like her than she is
    assert look(recognizer, neighbour).name == "Mama"
    recognizer.reject("Mama", *camera(recognizer, neighbour))
    again = look(recognizer, unit(neighbour + 0.1 * identity()))
    assert again.verdict is Verdict.UNCERTAIN and again.reason == "faces.rejected"
    for picture in views(mama, 10, 0.4):
        assert look(recognizer, picture).name == "Mama"  # Mama herself still is


def test_rejections_go_with_the_person(recognizer: Any) -> None:
    from pi_guard.detection.faces import EnrollmentError

    mama = identity()
    recognizer.enroll_many("Mama", views(mama, 5), "test")
    with pytest.raises(EnrollmentError, match=r"faces\.learn_not_enrolled"):
        recognizer.reject("Papa", *camera(recognizer, identity()))
    recognizer.reject("Mama", *camera(recognizer, identity()))
    assert recognizer.store.rename("Mama", "Mutti")
    assert [n for n, _ in recognizer.store.rejections()] == ["Mutti"]
    recognizer.delete("Mutti")
    assert recognizer.store.rejections() == []


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


def rows(front: int, left: int = 0, right: int = 0, match: float = 0.6) -> list[dict[str, Any]]:
    poses = ["front"] * front + ["left"] * left + ["right"] * right
    return [{"pose": p, "light": 60.0 + i * 10, "match": match} for i, p in enumerate(poses)]


def test_quality_grows_with_number_and_variety() -> None:
    assert enrollment_quality([], None)["score"] == 0
    few = enrollment_quality(rows(3), None)
    assert few["level"] == "weak" and few["views"] == {"front": 3, "left": 0, "right": 0}
    only_front = enrollment_quality(rows(20), None)
    varied = enrollment_quality(rows(10, 5, 5), None)
    assert varied["score"] > only_front["score"] and varied["level"] == "good"
    assert varied["tips"] == []
    keys = [t["key"] for t in only_front["tips"]]
    assert keys == ["faces.tip_left", "faces.tip_right"]
    assert only_front["tips"][0]["count"] == 3


def test_quality_warns_about_look_alikes_and_mixed_pictures() -> None:
    similar = enrollment_quality(rows(10, 5, 5), "Oma")
    assert similar["score"] <= 70 and similar["tips"][0] == {
        "key": "faces.tip_similar",
        "other": "Oma",
    }
    assert (
        enrollment_quality(rows(10, 5, 5, match=0.3), None)["tips"][0]["key"] == "faces.tip_mixed"
    )


def test_samples_from_before_poses_count_as_front() -> None:
    old = [{"pose": None, "light": None, "match": 0.6}] * 8
    assert enrollment_quality(old, None)["views"]["front"] == 8


def test_pose_comes_from_the_nose_between_the_eyes() -> None:
    def face(nose_x: float) -> np.ndarray:
        row = np.zeros(15, np.float32)
        row[4:10] = (40, 50, 80, 50, nose_x, 70)
        return row

    assert pose_of(face(61)) == "front"
    assert pose_of(face(70)) == "right" and pose_of(face(50)) == "left"


def test_pose_and_light_are_stored_and_old_databases_are_upgraded(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "bio.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            enrolled_at TEXT NOT NULL, enrolled_by TEXT NOT NULL);
        CREATE TABLE samples (id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL,
            embedding BLOB NOT NULL, created_at TEXT NOT NULL);
    """)
    conn.close()
    store = BiometricStore(path)
    store.add("Ben", with_context(identity(), "left", 80.0), "test", 10)
    store.add("Ben", identity(), "test", 10)  # plain embeddings still work
    first, second = store.details("Ben")
    assert (first["pose"], first["light"]) == ("left", 80.0) and second["pose"] is None
    store.close()


def test_context_survives_the_upload_checks() -> None:
    base = identity()
    samples = [with_context(v, "right", 100.0) for v in views(base, 4)]
    kept, _ = consistent(samples)
    assert all(getattr(s, "pose", None) == "right" for s in kept)


def test_a_profile_lives_and_dies_with_the_person(tmp_path: Path) -> None:
    store = BiometricStore(tmp_path / "bio.db")
    store.add("Ben", identity(), "test", 10)
    assert store.profile("Ben") == {"picture": False}
    assert store.set_profile("Ben", {"email": "ben@example.org"})
    assert store.set_picture("Ben", b"jpeg")
    assert store.profile("ben") == {"email": "ben@example.org", "picture": True}
    assert store.rename("Ben", "Benjamin") and store.picture("Benjamin") == b"jpeg"
    assert not store.set_profile("Niemand", {})
    assert store.delete("Benjamin")
    assert store.picture("Benjamin") is None
    store.close()


def test_people_from_before_profiles_are_upgraded(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "bio.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            enrolled_at TEXT NOT NULL, enrolled_by TEXT NOT NULL);
        CREATE TABLE samples (id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL,
            embedding BLOB NOT NULL, created_at TEXT NOT NULL);
        INSERT INTO people VALUES (1, 'Oma', 'x', 'y');
    """)
    conn.close()
    store = BiometricStore(path)
    assert store.profile("Oma") == {"picture": False}
    store.close()


def test_profile_fields_are_checked() -> None:
    from aiohttp import web

    from pi_guard.web.api_manage import clean_profile

    assert clean_profile({"email": " a@b.de ", "phone": "", "evil": "x"}) == {"email": "a@b.de"}
    assert clean_profile({"birthday": "1950-02-03"}) == {"birthday": "1950-02-03"}
    for bad in ({"birthday": "morgen"}, {"birthday": "2999-01-01"}, {"email": "kein at"}):
        with pytest.raises(web.HTTPBadRequest):
            clean_profile(bad)


def test_a_picture_loses_its_hidden_data_and_gets_small() -> None:
    import cv2

    from pi_guard.web.api_manage import PICTURE_SIZE, small_picture

    photo = (RNG.random((600, 900, 3)) * 255).astype(np.uint8)
    raw = bytes(cv2.imencode(".jpg", photo)[1])
    gps = b"\xff\xe1\x00\x10Exif\x00\x00GPSDATA!"  # an EXIF block right after the start
    small = small_picture(raw[:2] + gps + raw[2:])
    assert b"GPSDATA" not in small
    image = cv2.imdecode(np.frombuffer(small, np.uint8), cv2.IMREAD_COLOR)
    assert image.shape[:2] == (PICTURE_SIZE, PICTURE_SIZE)
