"""Frame-differencing motion detection on small grayscale frames.

Frames arrive at a few FPS and ~320 px wide from the capture pipeline, so each
one costs well under a millisecond on a Raspberry Pi 4 (see docs/performance.md).

False positives are reduced by: a slowly adapting background (gradual light
changes are absorbed), a morphological opening (sensor noise), a minimum
changed area, a minimum duration, and by treating a sudden change of most of
the frame (lights switched on, camera exposure jump) as a new background
rather than as motion.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import numpy.typing as npt

from pi_guard.config.files import CameraConfig
from pi_guard.core.timeutil import TimeWindow, in_any_window, utcnow
from pi_guard.detection.zones import build_mask

log = logging.getLogger(__name__)

Frame = npt.NDArray[np.uint8]

BACKGROUND_ALPHA = 0.05
GLOBAL_CHANGE_RATIO = 0.6
END_AFTER_SECONDS = 3.0


@dataclass(frozen=True)
class MotionParams:
    pixel_threshold: int
    min_area_percent: float
    min_duration: float

    @classmethod
    def from_camera(cls, camera: CameraConfig, global_sensitivity: int) -> MotionParams:
        sensitivity = camera.motion.sensitivity
        if sensitivity is None:
            sensitivity = global_sensitivity
        # 0 -> very insensitive (threshold 60, area x2), 100 -> very sensitive (threshold 10, x0.25)
        threshold = round(60 - sensitivity / 2)
        area_scale = 2.0 - 1.75 * sensitivity / 100
        return cls(
            threshold,
            camera.motion.min_area_percent * area_scale,
            camera.motion.min_duration_seconds,
        )


@dataclass(frozen=True)
class MotionStart:
    camera: str
    area_percent: float


@dataclass(frozen=True)
class MotionEnd:
    camera: str
    duration: float
    peak_area_percent: float


MotionCallback = Callable[[MotionStart | MotionEnd], None]


class MotionDetector:
    def __init__(
        self,
        camera: CameraConfig,
        global_sensitivity: int,
        on_change: MotionCallback,
        tz: ZoneInfo,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        import cv2

        self._cv2 = cv2
        self.camera = camera
        self.params = MotionParams.from_camera(camera, global_sensitivity)
        self._on_change = on_change
        self._tz = tz
        self._clock = clock
        self._schedule = [TimeWindow.parse(w) for w in camera.motion.schedule]
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        self._background: npt.NDArray[np.float32] | None = None
        self._mask: Frame | None = None
        self._mask_pixels = 0
        self._candidate_since: float | None = None
        self._active_since: float | None = None
        self._last_motion = 0.0
        self._peak = 0.0
        self.last_area_percent = 0.0

    @property
    def active(self) -> bool:
        return self._active_since is not None

    def _prepare(self, frame: Frame) -> None:
        height, width = frame.shape
        self._mask = build_mask(self.camera.motion.zones, width, height)
        self._mask_pixels = int(np.count_nonzero(self._mask)) or 1

    def area_percent(self, frame: Frame) -> float:
        """Changed share of the watched area; updates the background."""
        cv2 = self._cv2
        if self._mask is None or self._mask.shape != frame.shape:
            self._prepare(frame)
        blurred = cv2.GaussianBlur(frame, (5, 5), 0)
        if self._background is None:
            self._background = blurred.astype(np.float32)
            return 0.0
        diff = cv2.absdiff(blurred, cv2.convertScaleAbs(self._background))
        _, changed = cv2.threshold(diff, self.params.pixel_threshold, 255, cv2.THRESH_BINARY)
        changed = cv2.morphologyEx(changed, cv2.MORPH_OPEN, self._kernel)
        changed = cv2.bitwise_and(changed, self._mask)
        ratio = cv2.countNonZero(changed) / self._mask_pixels
        if ratio > GLOBAL_CHANGE_RATIO:
            self._background = blurred.astype(np.float32)  # light switch, exposure jump
            return 0.0
        cv2.accumulateWeighted(blurred, self._background, BACKGROUND_ALPHA)
        return float(ratio * 100)

    def process(self, frame: Frame, ts: float) -> None:
        if self._schedule and not in_any_window(self._schedule, self._clock().astimezone(self._tz)):
            self._finish(ts)
            self._background = None
            return
        area = self.last_area_percent = self.area_percent(frame)
        moving = area >= self.params.min_area_percent
        if moving:
            self._last_motion = ts
            self._peak = max(self._peak, area)
            if self._candidate_since is None:
                self._candidate_since = ts
            if (
                self._active_since is None
                and ts - self._candidate_since >= self.params.min_duration
            ):
                self._active_since = ts
                self._on_change(MotionStart(self.camera.id, round(self._peak, 2)))
        else:
            self._candidate_since = None
            if self._active_since is not None and ts - self._last_motion >= END_AFTER_SECONDS:
                self._finish(ts)
            elif self._active_since is None:
                self._peak = 0.0

    def _finish(self, ts: float) -> None:
        if self._active_since is not None:
            self._on_change(
                MotionEnd(self.camera.id, round(ts - self._active_since, 1), round(self._peak, 2))
            )
        self._active_since = None
        self._candidate_since = None
        self._peak = 0.0
