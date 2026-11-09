"""Is there a person in the picture? (Not *who* — see faces.py.)

Bodies:
  * YOLOX-Nano when the model is installed: finds people standing, sitting, from
    behind or far away. Measured on a Pi 4 at 0.3 s a picture, as fast as HOG.
  * otherwise OpenCV's built-in HOG pedestrian detector (no download), which finds
    upright, mostly full bodies and misses most people a home camera sees.
Faces: YuNet when the model is installed, on a 640 px copy of the whole picture and
again on the head of each body at full resolution, where faces are often too small
for the copy. A body or a face counts. Both miss people that are hidden or in the dark.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from pi_guard.detection.models import YOLOX, YUNET

log = logging.getLogger(__name__)

ANALYSIS_WIDTH = 640
# Recognition reads faces from the full picture (up to this width): a face that is
# 40 pixels wide after shrinking to 640 has 80 in a 1280 camera picture.
FULL_WIDTH = 1920
# YuNet rows: x, y, w, h, 5 landmarks (x, y), score. All but the score scale with the image.
_GEOMETRY = slice(0, 14)
HOG_MIN_WEIGHT = 0.5
FACE_MIN_SCORE = 0.8

YOLOX_SIZE = 416
PERSON_MIN_SCORE = 0.5
NMS_OVERLAP = 0.45
MAX_HEADS = 3  # bodies whose head is searched for a face, biggest first
MIN_HEAD_PX = 24  # a head region smaller than this holds no face worth finding

Image = npt.NDArray[np.uint8]
Box = tuple[float, float, float, float]  # x, y, w, h


def scale_faces(faces: npt.NDArray[np.float32], factor: float) -> npt.NDArray[np.float32]:
    scaled = faces.copy()
    scaled[:, _GEOMETRY] *= factor
    return scaled


def shift_faces(faces: npt.NDArray[np.float32], dx: float, dy: float) -> npt.NDArray[np.float32]:
    """Move YuNet rows found in a crop back into the coordinates of the whole picture."""
    shifted = faces.copy()
    shifted[:, [0, 4, 6, 8, 10, 12]] += dx  # x of the box and of each landmark, not w and h
    shifted[:, [1, 5, 7, 9, 11, 13]] += dy
    return shifted


def head_region(body: Box, width: int, height: int) -> tuple[int, int, int, int] | None:
    """Where the face of a body should be: its upper part, a little wider. None if tiny."""
    x, y, w, h = body
    left, right = max(0.0, x - 0.15 * w), min(float(width), x + 1.15 * w)
    top, bottom = max(0.0, y - 0.1 * h), min(float(height), y + 0.6 * h)
    if min(right - left, bottom - top) < MIN_HEAD_PX:
        return None
    return round(left), round(top), round(right), round(bottom)


def merge_faces(
    whole: npt.NDArray[np.float32] | None, heads: list[npt.NDArray[np.float32]]
) -> npt.NDArray[np.float32] | None:
    """All faces once. A face found in a head crop replaces the same face from the small
    copy, because its landmarks were found at full resolution."""
    found = [row for crop in heads for row in crop]
    for row in [] if whole is None else whole:
        cx, cy = row[0] + row[2] / 2, row[1] + row[3] / 2
        if not any(abs(cx - f[0] - f[2] / 2) < f[2] / 2 and abs(cy - f[1] - f[3] / 2) < f[3] / 2
                   for f in found):  # fmt: skip
            found.append(row)
    if not found:
        return whole
    return np.stack(found).astype(np.float32)


def _yolox_grid(size: int) -> tuple[npt.NDArray[np.float32], npt.NDArray[np.float32]]:
    grids, strides = [], []
    for stride in (8, 16, 32):
        cells = size // stride
        ys, xs = np.meshgrid(np.arange(cells), np.arange(cells), indexing="ij")
        grids.append(np.stack((xs, ys), 2).reshape(-1, 2))
        strides.append(np.full((cells * cells, 1), stride))
    return np.concatenate(grids).astype(np.float32), np.concatenate(strides).astype(np.float32)


def decode_yolox(
    out: npt.NDArray[np.float32], ratio: float, size: int = YOLOX_SIZE
) -> tuple[list[Box], list[float]]:
    """People in YOLOX output: rows of (cx, cy, w, h) per grid cell, objectness, then one
    score per class; class 0 is "person". Boxes come back in picture coordinates."""
    grid, stride = _yolox_grid(size)
    centre = (out[:, :2] + grid) * stride
    size_ = np.exp(np.minimum(out[:, 2:4], 10.0)) * stride
    scores = out[:, 4] * out[:, 5]
    keep = scores >= PERSON_MIN_SCORE
    boxes = np.concatenate((centre - size_ / 2, size_), 1).astype(np.float32)[keep] / ratio
    return [_box(b) for b in boxes], [float(s) for s in scores[keep]]


# The 80 classes YOLOX learned from COCO, in its order; the Pi's person checks use only 0.
COCO = ("person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
        "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat",
        "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack",
        "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball",
        "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
        "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
        "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
        "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
        "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
        "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier",
        "toothbrush")  # fmt: skip
OBJECT_MIN_SCORE = 0.45


def decode_objects(
    out: npt.NDArray[np.float32], ratio: float, size: int = YOLOX_SIZE
) -> tuple[list[Box], list[float], list[int]]:
    """Every class: boxes in picture coordinates, scores, class numbers."""
    grid, stride = _yolox_grid(size)
    centre = (out[:, :2] + grid) * stride
    size_ = np.exp(np.minimum(out[:, 2:4], 10.0)) * stride
    classes = np.argmax(out[:, 5:], axis=1)
    scores = out[:, 4] * out[np.arange(len(out)), 5 + classes]
    keep = scores >= OBJECT_MIN_SCORE
    boxes = np.concatenate((centre - size_ / 2, size_), 1).astype(np.float32)[keep] / ratio
    return ([_box(b) for b in boxes], [float(s) for s in scores[keep]],
            [int(c) for c in classes[keep]])  # fmt: skip


def _box(values: Any) -> Box:
    x, y, w, h = (float(v) for v in values[:4])
    return x, y, w, h


@dataclass(frozen=True)
class PersonResult:
    bodies: int
    faces: npt.NDArray[np.float32] | None = None  # YuNet rows, in `image` coordinates
    image: Image | None = field(default=None, repr=False)  # full resolution
    seconds: float = 0.0
    detector: str = "hog"  # what found the bodies: "yolox" or "hog"
    hog_bodies: int | None = None  # HOG's count beside YOLOX, when comparing

    @property
    def face_count(self) -> int:
        return 0 if self.faces is None else len(self.faces)

    @property
    def found(self) -> bool:
        return self.bodies > 0 or self.face_count > 0


class PersonDetector:
    def __init__(self, models_dir: Path, compare: bool = False) -> None:
        import cv2

        self._cv2 = cv2
        # OpenCV models must not run in two threads at once; photos are framed meanwhile.
        self._lock = threading.Lock()
        self._hog = cv2.HOGDescriptor()
        self._hog.setSVMDetector(cv2.HOGDescriptor.getDefaultPeopleDetector())
        self._yolox: Any = None
        if YOLOX.present(models_dir):
            self._yolox = cv2.dnn.readNet(str(YOLOX.path(models_dir)))
        else:
            log.info("YOLOX model not installed; bodies are found by the weaker HOG detector "
                     "(run `pi-guard models download` to add it)")  # fmt: skip
        self.compare = compare and self._yolox is not None
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

    @property
    def body_detector(self) -> str:
        return "yolox" if self._yolox is not None else "hog"

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
        with self._lock:
            self._faces.setInputSize((width, height))
            _, faces = self._faces.detect(image)
        return faces  # type: ignore[no-any-return]

    def _hog_bodies(self, small: Image) -> list[Box]:
        with self._lock:
            rects, weights = self._hog.detectMultiScale(small, winStride=(8, 8), scale=1.05)
        if not len(rects):
            return []
        return [_box(r) for r, w in zip(rects, np.ravel(weights), strict=False)
                if w >= HOG_MIN_WEIGHT]  # fmt: skip

    def _yolox_bodies(self, image: Image) -> list[Box]:
        cv2 = self._cv2
        height, width = image.shape[:2]
        ratio = min(YOLOX_SIZE / height, YOLOX_SIZE / width)
        resized = cv2.resize(image, (round(width * ratio), round(height * ratio)))
        padded = np.full((YOLOX_SIZE, YOLOX_SIZE, 3), 114, np.uint8)  # the model's own padding
        padded[: resized.shape[0], : resized.shape[1]] = resized
        with self._lock:
            self._yolox.setInput(cv2.dnn.blobFromImage(padded, 1.0, (YOLOX_SIZE, YOLOX_SIZE)))
            out = self._yolox.forward()[0]
        boxes, scores = decode_yolox(out, ratio)
        if not boxes:
            return []
        keep = cv2.dnn.NMSBoxes(boxes, scores, PERSON_MIN_SCORE, NMS_OVERLAP)
        return [boxes[i] for i in np.ravel(keep)]

    def objects(self, image: Image) -> list[tuple[Box, str, float]]:
        """People and things in `image` (YOLOX only), for marking the live picture."""
        if self._yolox is None:
            return []
        cv2 = self._cv2
        height, width = image.shape[:2]
        ratio = min(YOLOX_SIZE / height, YOLOX_SIZE / width)
        resized = cv2.resize(image, (round(width * ratio), round(height * ratio)))
        padded = np.full((YOLOX_SIZE, YOLOX_SIZE, 3), 114, np.uint8)
        padded[: resized.shape[0], : resized.shape[1]] = resized
        with self._lock:
            self._yolox.setInput(cv2.dnn.blobFromImage(padded, 1.0, (YOLOX_SIZE, YOLOX_SIZE)))
            out = self._yolox.forward()[0]
        boxes, scores, classes = decode_objects(out, ratio)
        if not boxes:
            return []
        keep = cv2.dnn.NMSBoxesBatched(boxes, scores, classes, OBJECT_MIN_SCORE, NMS_OVERLAP)
        return [(boxes[i], COCO[classes[i]], scores[i]) for i in np.ravel(keep)]

    def bodies(self, image: Image) -> list[Box]:
        """People in `image`, in its coordinates."""
        if self._yolox is not None:
            return self._yolox_bodies(image)
        small = self.shrink(image, ANALYSIS_WIDTH)
        factor = image.shape[1] / small.shape[1]
        return [(x * factor, y * factor, w * factor, h * factor)
                for x, y, w, h in self._hog_bodies(small)]  # fmt: skip

    def _head_faces(self, image: Image, bodies: list[Box]) -> list[npt.NDArray[np.float32]]:
        height, width = image.shape[:2]
        found = []
        for body in sorted(bodies, key=lambda b: b[2] * b[3], reverse=True)[:MAX_HEADS]:
            region = head_region(body, width, height)
            if region is None:
                continue
            left, top, right, bottom = region
            faces = self.faces_full(image[top:bottom, left:right])
            if faces is not None and len(faces):
                found.append(shift_faces(faces, left, top))
        return found

    def detect(self, path: Path) -> PersonResult:
        started = time.monotonic()
        full = self.load(path, FULL_WIDTH)
        image = self.shrink(full, ANALYSIS_WIDTH)
        bodies = self.bodies(full)
        hog = len(self._hog_bodies(image)) if self.compare else None
        faces = self.faces(image)
        if faces is not None and len(faces) and image is not full:
            faces = scale_faces(faces, full.shape[1] / image.shape[1])
        if self._faces is not None and image is not full:
            faces = merge_faces(faces, self._head_faces(full, bodies))
        return PersonResult(len(bodies), faces, full, time.monotonic() - started,
                            self.body_detector, hog)  # fmt: skip

    def locate(self, image: Image) -> tuple[int, int, int, int] | None:
        """Box (x, y, w, h) of the most prominent person in `image`, faces first."""
        faces = self.faces_full(image)
        boxes: list[Box] = []
        if faces is not None and len(faces):
            boxes = [_box(row) for row in faces]
        else:
            boxes = self.bodies(image)
        if not boxes:
            return None
        x, y, w, h = max(boxes, key=lambda b: b[2] * b[3])
        return round(x), round(y), round(w), round(h)
