from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from pi_guard.config.files import FaceSettings
from pi_guard.core.events import Event, EventType
from pi_guard.core.modes import Mode
from pi_guard.detection.enrollment import EnrollmentSession, video_frames
from pi_guard.detection.faces import BiometricStore, EnrollmentError
from pi_guard.messaging.base import Incoming

from .helpers import ADMIN, VIEWER, fake_snapshot, make_app, msg


class FakeRecognizer:
    """Gives every image a distinct random embedding; images named *noface* fail."""

    def __init__(self, store: BiometricStore) -> None:
        self.store = store
        self.settings = FaceSettings()
        self._rng = np.random.default_rng(0)
        self.prepared: list[Path] = []

    def prepare(self, photo: Path) -> np.ndarray:
        self.prepared.append(photo)
        if "noface" in photo.name:
            raise EnrollmentError("faces.no_face")
        vector = self._rng.normal(size=128).astype(np.float32)
        return vector / np.linalg.norm(vector)

    def enroll(self, name: str, embedding: np.ndarray, actor: str) -> int:
        return self.store.add(name, embedding, actor, self.settings.max_samples_per_person)

    def quality(self, name: str) -> dict[str, Any]:
        return {"score": 50, "level": "ok", "views": {}, "tips": [{"key": "faces.tip_light"}]}

    def enroll_many(self, name: str, samples: list[np.ndarray], actor: str) -> Any:
        from pi_guard.detection.faces import EnrollReport

        report = EnrollReport()
        for sample in samples:
            report.total = self.enroll(name, sample, actor)
            report.stored += 1
        return report

    def delete(self, name: str) -> bool:
        return self.store.delete(name)


@pytest.fixture
def app(tmp_path: Path):  # type: ignore[no-untyped-def]
    application = make_app(tmp_path)
    store = BiometricStore(tmp_path / "bio.db")
    recognizer = FakeRecognizer(store)

    class Analysis:
        faces = recognizer

    application.analysis = Analysis()  # type: ignore[assignment]
    yield application
    store.close()
    application.db.close()


def upload(tmp_path: Path, name: str, kind: str = "photo", caption: str = "") -> Incoming:
    path = tmp_path / name
    path.write_bytes(b"x")
    return Incoming("telegram", ADMIN, ADMIN, caption, attachment=path, attachment_kind=kind)  # type: ignore[arg-type]


def last(app) -> Any:  # type: ignore[no-untyped-def]
    return app.providers["telegram"].last()


def commands(reply: Any) -> list[str]:
    return [b.command for row in reply.buttons for b in row]


async def press(app, command: str) -> None:  # type: ignore[no-untyped-def]
    await app.messenger.handle(msg(command, ADMIN, from_button=True))


async def test_photo_series_with_consent(app, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    await app.messenger.handle(upload(tmp_path, "a.jpg", caption="/eintragen Oma Erna"))
    assert "Oma Erna" in last(app).text and "1" in last(app).text
    await app.messenger.handle(upload(tmp_path, "b.jpg"))  # no caption: joins the session
    await app.messenger.handle(upload(tmp_path, "noface.jpg"))
    assert "cannot find a face" in last(app).text
    assert not (tmp_path / "a.jpg").exists()  # uploads are deleted after use
    store = app.faces.store
    assert store.people() == []
    await press(app, "enroll done")
    assert "agreed" in last(app).text and "2 picture" in last(app).text
    await press(app, "enroll confirm")
    assert store.people()[0][:2] == ("Oma Erna", 2)


async def test_bot_asks_for_the_name(app, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    await app.messenger.handle(msg("/eintragen", ADMIN))
    assert "name" in last(app).text
    await app.messenger.handle(msg("Opa Hans", ADMIN))
    assert "Enrolling: Opa Hans" in last(app).text


async def test_cancel_stores_nothing(app, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    await app.messenger.handle(upload(tmp_path, "a.jpg", caption="/enroll Erna"))
    await press(app, "enroll cancel")
    await press(app, "enroll confirm")
    assert "expired" in last(app).text
    assert app.faces.store.people() == []


async def test_photo_without_session_explains_what_to_do(app, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    await app.messenger.handle(upload(tmp_path, "a.jpg"))
    assert "Add a person" in last(app).text


async def test_picture_from_the_camera(app) -> None:  # type: ignore[no-untyped-def]
    fake_snapshot(app)
    await app.messenger.handle(msg("/enroll Erna", ADMIN))
    assert "enroll camera" in commands(last(app))
    await press(app, "enroll camera")
    assert "Picture added" in last(app).text
    assert list((app.media_dir / "snapshots").glob("*enroll*")) == []  # not kept


async def test_alert_offers_enrollment_to_admins_only(app) -> None:  # type: ignore[no-untyped-def]
    fake_snapshot(app)
    app.modes.set_manual(Mode.AWAY)
    snapshot = await app.take_photo("living_room", prefix="unknown_person")
    await app.notifier.handle(
        Event(EventType.UNKNOWN_PERSON, camera="living_room", snapshot=snapshot, data={"faces": 1})
    )
    while not app.notifier._queue.empty():
        await app.notifier._deliver(app.notifier._queue.get_nowait())
    sent = {chat: reply for chat, reply in app.providers["telegram"].sent}
    trust = [c for c in commands(sent[ADMIN]) if c.startswith("trust ")]
    assert trust and not [c for c in commands(sent[VIEWER]) if c.startswith("trust ")]
    await press(app, trust[0])
    assert "name" in last(app).text
    await app.messenger.handle(msg("Nachbarin Petra", ADMIN))
    assert "Enrolling: Nachbarin Petra (1 picture" in last(app).text


async def test_session_skips_duplicates_and_respects_limit() -> None:
    session = EnrollmentSession(name="x")
    a = np.ones(128, dtype=np.float32) / np.sqrt(128)
    assert session.add(a, 2)
    assert not session.add(a.copy(), 2)
    b = np.zeros(128, dtype=np.float32)
    b[0] = 1
    c = np.zeros(128, dtype=np.float32)
    c[1] = 1
    assert session.add(b, 2)
    assert not session.add(c, 2)


@pytest.mark.ffmpeg
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
async def test_video_frames_and_video_enrollment(app, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    video = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=10:duration=4",
            str(video),
        ],
        check=True,
    )
    workdir, frames = await video_frames(video, count=6)
    assert 5 <= len(frames) <= 6
    shutil.rmtree(workdir)

    await app.messenger.handle(msg("/enroll Erna", ADMIN))
    incoming = Incoming("telegram", ADMIN, ADMIN, "", attachment=video, attachment_kind="video")
    await app.messenger.handle(incoming)
    assert "picture(s) taken from the video" in last(app).text
    assert all(not p.exists() for p in app.faces.prepared)  # frames removed
