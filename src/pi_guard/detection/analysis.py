"""What happens after motion starts: snapshot, event, optional person check.

Person detection and recognition run in a worker thread, one image at a time,
and only on motion — never on the continuous stream. If a check is too slow
for the hardware repeatedly, recognition switches itself off.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from pi_guard.cameras.pipeline import SnapshotUnavailableError
from pi_guard.core.events import Event, EventType
from pi_guard.detection.faces import FaceRecognizer, Verdict
from pi_guard.detection.motion import MotionEnd, MotionStart
from pi_guard.detection.persons import PersonDetector

if TYPE_CHECKING:
    from pi_guard.app import App

log = logging.getLogger(__name__)

PERSON_ATTEMPTS = 3
ATTEMPT_SPACING_SECONDS = 2.0
SLOW_SECONDS = 5.0
SLOW_STRIKES = 3


class MotionAnalysis:
    def __init__(
        self, app: App, persons: PersonDetector | None, faces: FaceRecognizer | None
    ) -> None:
        self._app = app
        self._persons = persons
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
        if self._persons is None or snapshot is None:
            return
        interval = self._app.config.file.faces.min_interval_seconds
        now = time.monotonic()
        if now - self._last_check.get(change.camera, -interval) < interval:
            return
        self._last_check[change.camera] = now
        for attempt in range(PERSON_ATTEMPTS):
            if attempt:
                await asyncio.sleep(ATTEMPT_SPACING_SECONDS)
                detector = self._app.motion.get(change.camera)
                if detector is None or not detector.active:
                    return
                snapshot = await self._snapshot(change.camera, "person")
                if snapshot is None:
                    return
            event = await self._check(change.camera, snapshot)
            if event is not None:
                self._app.bus.publish(event)
                return

    async def _check(self, camera: str, snapshot: Path) -> Event | None:
        assert self._persons is not None
        async with self._worker:
            started = time.monotonic()
            try:
                result = await asyncio.to_thread(self._persons.detect, snapshot)
                identities = []
                if self.faces is not None and result.faces is not None and result.image is not None:
                    image = result.image
                    identities = [
                        await asyncio.to_thread(self.faces.identify, image, face)
                        for face in result.faces
                    ]
            except Exception:
                log.exception("person check failed")
                return None
            self._watch_speed(time.monotonic() - started)
        if not result.found:
            return None
        known = [i for i in identities if i.verdict is Verdict.KNOWN]
        if known:
            best = max(known, key=lambda i: i.score)
            return Event(
                EventType.KNOWN_PERSON,
                camera=camera,
                snapshot=snapshot,
                data={"name": best.name or "", "score": round(best.score, 2)},
            )
        if any(i.verdict is Verdict.UNCERTAIN for i in identities):
            return Event(
                EventType.PERSON_DETECTED,
                camera=camera,
                snapshot=snapshot,
                data={"recognition": "uncertain"},
            )
        if identities:
            return Event(
                EventType.UNKNOWN_PERSON,
                camera=camera,
                snapshot=snapshot,
                data={"faces": len(identities)},
            )
        return Event(
            EventType.PERSON_DETECTED,
            camera=camera,
            snapshot=snapshot,
            data={"bodies": result.bodies, "faces": result.face_count},
        )

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
