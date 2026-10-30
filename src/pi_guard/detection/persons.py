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
HOG_MIN_WEIGHT = 0.5
FACE_MIN_SCORE = 0.8

Image = npt.NDArray[np.uint8]


@dataclass(frozen=True)
class PersonResult:
    bodies: int
    faces: npt.NDArray[np.float32] | None = None  # YuNet rows, in `image` coordinates
    image: Image | None = field(default=None, repr=False)
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

    def load(self, path: Path) -> Image:
        cv2 = self._cv2
        image = cv2.imread(str(path))
        if image is None:
            raise ValueError(f"cannot read image {path.name}")
        height, width = image.shape[:2]
        if width > ANALYSIS_WIDTH:
            image = cv2.resize(image, (ANALYSIS_WIDTH, round(height * ANALYSIS_WIDTH / width)))
        return image  # type: ignore[no-any-return]

    def faces(self, image: Image) -> npt.NDArray[np.float32] | None:
        if self._faces is None:
            return None
        height, width = image.shape[:2]
        self._faces.setInputSize((width, height))
        _, faces = self._faces.detect(image)
        return faces  # type: ignore[no-any-return]

    def detect(self, path: Path) -> PersonResult:
        started = time.monotonic()
        image = self.load(path)
        rects, weights = self._hog.detectMultiScale(image, winStride=(8, 8), scale=1.05)
        bodies = int(sum(1 for w in np.ravel(weights) if w >= HOG_MIN_WEIGHT)) if len(rects) else 0
        faces = self.faces(image)
        return PersonResult(bodies, faces, image, time.monotonic() - started)
