"""Is there a person in the picture? (Not *who* — see faces.py.)

Two lightweight detectors, both run on a 640 px wide copy of a snapshot:
  * OpenCV's built-in HOG pedestrian detector (no download; finds upright,
    mostly full bodies, misses close-ups)
  * YuNet face detection when the model is installed (finds faces looking
    roughly towards the camera)
Either one counts. Both miss people that are small, occluded or in the dark.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from pi_guard.detection.models import YUNET

log = logging.getLogger(__name__)

ANALYSIS_WIDTH = 640
# Recognition reads faces from the full picture (up to this width): a face that is
# 40 pixels wide after shrinking to 640 has 80 in a 1280 camera picture.
FULL_WIDTH = 1920
# YuNet rows: x, y, w, h, 5 landmarks (x, y), score. All but the score scale with the image.
_GEOMETRY = slice(0, 14)
HOG_MIN_WEIGHT = 0.5
FACE_MIN_SCORE = 0.8

Image = npt.NDArray[np.uint8]


def scale_faces(faces: npt.NDArray[np.float32], factor: float) -> npt.NDArray[np.float32]:
    scaled = faces.copy()
    scaled[:, _GEOMETRY] *= factor
    return scaled


@dataclass(frozen=True)
class PersonResult:
    bodies: int
    faces: npt.NDArray[np.float32] | None = None  # YuNet rows, in `image` coordinates
    image: Image | None = field(default=None, repr=False)  # full resolution
    seconds: float = 0.0

    @property
    def face_count(self) -> int:
        return 0 if self.faces is None else len(self.faces)

    @property
    def found(self) -> bool:
        return self.bodies > 0 or self.face_count > 0


class PersonDetector:
    def __init__(self, models_dir: Path) -> None:
        import cv2

        self._cv2 = cv2
        self._hog = cv2.HOGDescriptor()
        self._hog.setSVMDetector(cv2.HOGDescriptor.getDefaultPeopleDetector())
        self._faces: Any = None
        if YUNET.present(models_dir):
            self._faces = cv2.FaceDetectorYN.create(
                str(YUNET.path(models_dir)), "", (320, 320), FACE_MIN_SCORE
            )
        else:
            log.info(
                "YuNet model not installed; person detection uses the body detector only "
                "(run `pi-guard models download` to add face detection)"
            )

    @property
    def detects_faces(self) -> bool:
        return self._faces is not None

    def load(self, path: Path, width: int = ANALYSIS_WIDTH) -> Image:
        cv2 = self._cv2
        image = cv2.imread(str(path))
        if image is None:
            raise ValueError(f"cannot read image {path.name}")
        return self.shrink(image, width)

    def shrink(self, image: Image, width: int) -> Image:
        height, current = image.shape[:2]
        if current <= width:
            return image
        return self._cv2.resize(  # type: ignore[no-any-return]
            image, (width, round(height * width / current)), interpolation=self._cv2.INTER_AREA
        )

    def faces_full(
        self, image: Image, width: int = ANALYSIS_WIDTH
    ) -> npt.NDArray[np.float32] | None:
        """Find faces on a smaller copy (fast), return them in `image` coordinates."""
        small = self.shrink(image, width)
        faces = self.faces(small)
        if faces is None or not len(faces) or small is image:
            return faces
        return scale_faces(faces, image.shape[1] / small.shape[1])

    def faces(self, image: Image) -> npt.NDArray[np.float32] | None:
        if self._faces is None:
            return None
        height, width = image.shape[:2]
        self._faces.setInputSize((width, height))
        _, faces = self._faces.detect(image)
        return faces  # type: ignore[no-any-return]

    def detect(self, path: Path) -> PersonResult:
        started = time.monotonic()
        full = self.load(path, FULL_WIDTH)
        image = self.shrink(full, ANALYSIS_WIDTH)
        rects, weights = self._hog.detectMultiScale(image, winStride=(8, 8), scale=1.05)
        bodies = int(sum(1 for w in np.ravel(weights) if w >= HOG_MIN_WEIGHT)) if len(rects) else 0
        faces = self.faces(image)
        if faces is not None and len(faces) and image is not full:
            faces = scale_faces(faces, full.shape[1] / image.shape[1])
        return PersonResult(bodies, faces, full, time.monotonic() - started)

    def locate(self, image: Image) -> tuple[int, int, int, int] | None:
        """Box (x, y, w, h) of the most prominent person in `image`, faces first."""
        cv2 = self._cv2
        height, width = image.shape[:2]
        scale = width / ANALYSIS_WIDTH if width > ANALYSIS_WIDTH else 1.0
        small = (
            image if scale == 1.0 else cv2.resize(image, (ANALYSIS_WIDTH, round(height / scale)))
        )
        boxes: list[tuple[float, float, float, float]] = []
        faces = self.faces(small)
        if faces is not None and len(faces):
            boxes = [tuple(row[:4]) for row in faces]
        else:
            rects, weights = self._hog.detectMultiScale(small, winStride=(8, 8), scale=1.05)
            boxes = [
                tuple(r)
                for r, w in zip(rects, np.ravel(weights), strict=False)
                if w >= HOG_MIN_WEIGHT
            ]
        if not boxes:
            return None
        x, y, w, h = max(boxes, key=lambda b: b[2] * b[3])
        return round(x * scale), round(y * scale), round(w * scale), round(h * scale)
