"""What happens after motion starts: snapshot, event, optional person check.

Person detection and recognition run in a worker thread, one image at a time,
and only on motion — never on the continuous stream. If a check is too slow
for the hardware repeatedly, recognition switches itself off.

One picture often shows a face turned away or blurred, so up to PERSON_ATTEMPTS
pictures are judged together (see `Sighting`): a clear answer ends the check at
once, anything else waits for the next picture.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pi_guard.cameras.pipeline import SnapshotUnavailableError
from pi_guard.core.events import Event, EventType
from pi_guard.detection.faces import FaceRecognizer, Identification, Verdict
from pi_guard.detection.motion import MotionEnd, MotionStart
from pi_guard.detection.persons import PersonDetector, PersonResult

if TYPE_CHECKING:
    from pi_guard.app import App

log = logging.getLogger(__name__)

PERSON_ATTEMPTS = 3
ATTEMPT_SPACING_SECONDS = 2.0
SLOW_SECONDS = 5.0
SLOW_STRIKES = 3
# An unclear face that matches nobody in this many pictures counts as a stranger.
UNKNOWN_PICTURES = 2


@dataclass(frozen=True)
class Frame:
    camera: str
    snapshot: Path
    result: PersonResult
    identities: list[Identification] | None  # None: recognition is off

    def data(self) -> dict[str, Any]:
        data: dict[str, Any] = {"bodies": self.result.bodies, "faces": self.result.face_count,
                                "detector": self.result.detector}  # fmt: skip
        if self.result.hog_bodies is not None:
            data["hog"] = self.result.hog_bodies
        return data


@dataclass
class Sighting:
    """What the pictures of one motion showed so far, and what to report.

    Decided at once: a clear stranger, a stranger next to a family member, a picture
    in which every face is a family member, or a person while recognition is off.
    Everything else waits for the last picture: an unclear face that matched nobody
    twice is a stranger, a family member seen once is known, anything else is a person.
    A person is a high alarm when away as well, so waiting never hides one.
    """

    pictures: int = 0
    persons: list[Frame] = field(default_factory=list)
    known: list[Frame] = field(default_factory=list)
    unknown: list[Frame] = field(default_factory=list)
    uncertain: list[Frame] = field(default_factory=list)

    def add(self, frame: Frame) -> Event | None:
        self.pictures += 1
        if not frame.result.found:
            return None
        if frame.identities is None:
            return self._person(frame)
        known = [i for i in frame.identities if i.verdict is Verdict.KNOWN]
        unknown = [i for i in frame.identities if i.verdict is Verdict.UNKNOWN]
        if unknown and (known or any(i.clear for i in unknown)):
            return self._stranger(frame)
        if known and len(known) == len(frame.identities):
            return self._known(frame)
        self.persons.append(frame)
        if unknown:
            self.unknown.append(frame)
        if known:
            self.known.append(frame)
        if any(i.verdict is Verdict.UNCERTAIN for i in frame.identities):
            self.uncertain.append(frame)
        return None

    def final(self) -> Event | None:
        if len(self.unknown) >= UNKNOWN_PICTURES:
            return self._stranger(self.unknown[0])
        if self.known:
            return self._known(max(self.known, key=_best_known))
        if self.unknown or self.uncertain:
            return self._person((self.unknown or self.uncertain)[0], recognition="uncertain")
        if self.persons:
            return self._person(max(self.persons, key=lambda f: (f.result.face_count,
                                                                   f.result.bodies)))  # fmt: skip
        return None

    def _known(self, frame: Frame) -> Event:
        best = max((i for i in frame.identities or [] if i.verdict is Verdict.KNOWN),
                   key=lambda i: i.score)  # fmt: skip
        data = {**frame.data(), "name": best.name or "", "score": round(best.score, 2),
                "pictures": self.pictures}  # fmt: skip
        return Event(EventType.KNOWN_PERSON, camera=frame.camera, snapshot=frame.snapshot,
                     data=data)  # fmt: skip

    def _stranger(self, frame: Frame) -> Event:
        identities = frame.identities or []
        known = sorted({i.name for i in identities if i.verdict is Verdict.KNOWN and i.name})
        data = {**frame.data(), "faces": len(identities), "known": known,
                "pictures": self.pictures}  # fmt: skip
        return Event(EventType.UNKNOWN_PERSON, camera=frame.camera, snapshot=frame.snapshot,
                     data=data)  # fmt: skip

    def _person(self, frame: Frame, recognition: str | None = None) -> Event:
        data = {**frame.data(), "pictures": self.pictures}
        if recognition:
            data["recognition"] = recognition
        return Event(EventType.PERSON_DETECTED, camera=frame.camera, snapshot=frame.snapshot,
                     data=data)  # fmt: skip


def _best_known(frame: Frame) -> float:
    return max(i.score for i in frame.identities or [] if i.verdict is Verdict.KNOWN)


class MotionAnalysis:
    def __init__(
        self, app: App, persons: PersonDetector | None, faces: FaceRecognizer | None
    ) -> None:
        self._app = app
        self.persons = persons
        self.faces = faces
        self._worker = asyncio.Semaphore(1)
        self._last_check: dict[str, float] = {}
        self._slow_strikes = 0
        self._tasks: set[asyncio.Task[None]] = set()

    def on_change(self, change: MotionStart | MotionEnd) -> None:
        if isinstance(change, MotionEnd):
            self._app.bus.publish(
                Event(
                    EventType.MOTION_ENDED,
                    camera=change.camera,
                    data={"duration": change.duration, "peak_area": change.peak_area_percent},
                )
            )
            return
        task = asyncio.get_running_loop().create_task(self._started(change))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _snapshot(self, camera: str, prefix: str) -> Path | None:
        try:
            return await self._app.take_photo(camera, prefix=prefix)
        except SnapshotUnavailableError:
            return None

    async def _started(self, change: MotionStart) -> None:
        snapshot = await self._snapshot(change.camera, "motion")
        self._app.bus.publish(
            Event(
                EventType.MOTION_DETECTED,
                camera=change.camera,
                data={"area": change.area_percent},
                snapshot=snapshot,
            )
        )
        if self.persons is None or snapshot is None:
            return
        interval = self._app.config.file.faces.min_interval_seconds
        now = time.monotonic()
        if now - self._last_check.get(change.camera, -interval) < interval:
            return
        self._last_check[change.camera] = now
        sighting = Sighting()
        for attempt in range(PERSON_ATTEMPTS):
            if attempt:
                await asyncio.sleep(ATTEMPT_SPACING_SECONDS)
                detector = self._app.motion.get(change.camera)
                if detector is None or not detector.active:
                    break
                snapshot = await self._snapshot(change.camera, "person")
                if snapshot is None:
                    break
            frame = await self._check(change.camera, snapshot)
            if frame is None:
                continue
            event = sighting.add(frame)
            if event is not None:
                self._app.bus.publish(event)
                return
        event = sighting.final()
        if event is not None:
            self._app.bus.publish(event)

    async def _check(self, camera: str, snapshot: Path) -> Frame | None:
        assert self.persons is not None
        async with self._worker:
            started = time.monotonic()
            faces = self.faces
            try:
                result = await asyncio.to_thread(self.persons.detect, snapshot)
                identities: list[Identification] | None = None if faces is None else []
                if faces is not None and result.faces is not None and result.image is not None:
                    image = result.image
                    identities = [
                        await asyncio.to_thread(faces.identify, image, face)
                        for face in result.faces
                    ]
            except Exception:
                log.exception("person check failed")
                return None
            self._watch_speed(time.monotonic() - started)
        return Frame(camera, snapshot, result, identities)

    def _watch_speed(self, seconds: float) -> None:
        if self.faces is None:
            return
        self._slow_strikes = self._slow_strikes + 1 if seconds > SLOW_SECONDS else 0
        if self._slow_strikes >= SLOW_STRIKES:
            log.warning(
                "face recognition took over %.0fs %d times in a row; disabling it "
                "(person detection stays on)",
                SLOW_SECONDS,
                SLOW_STRIKES,
            )
            self.faces = None
