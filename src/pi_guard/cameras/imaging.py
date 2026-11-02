"""Making snapshots easier to see: sharpest frame, digital zoom, brightening.

All of this works on the camera's own pictures; it cannot add detail the
sensor did not capture. Zoom is digital (crop and enlarge).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

Image = npt.NDArray[np.uint8]
Box = tuple[int, int, int, int]  # x, y, width, height

MIN_CROP_WIDTH = 160
# 3x3 grid, row by row: top-left ... bottom-right.
REGIONS = ("top_left", "top", "top_right", "left", "center", "right",
           "bottom_left", "bottom", "bottom_right")  # fmt: skip


def _cv2() -> Any:
    import cv2

    return cv2


def load(path: Path) -> Image:
    image = _cv2().imread(str(path))
    if image is None:
        raise ValueError(f"cannot read {path.name}")
    return image  # type: ignore[no-any-return]


def save(image: Image, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not _cv2().imwrite(str(path), image, [_cv2().IMWRITE_JPEG_QUALITY, 92]):
        raise ValueError(f"cannot write {path.name}")
    return path


def sharpness(image: Image) -> float:
    """Variance of the Laplacian: higher means more edges in focus, less motion blur."""
    cv2 = _cv2()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def sharpest(paths: list[Path]) -> Path:
    return max(paths, key=lambda p: sharpness(load(p)))


def sharpen(image: Image, amount: float = 0.8) -> Image:
    """Unsharp mask: mild enough not to create halos on faces."""
    cv2 = _cv2()
    blurred = cv2.GaussianBlur(image, (0, 0), 2.0)
    return cv2.addWeighted(image, 1 + amount, blurred, -amount, 0)  # type: ignore[no-any-return]


def is_dark(image: Image) -> bool:
    cv2 = _cv2()
    return float(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).mean()) < 80


def brighten(image: Image) -> Image:
    """Local contrast (CLAHE) on lightness plus a gamma lift for dark pictures."""
    cv2 = _cv2()
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    lightness, a, b = cv2.split(lab)
    lightness = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(lightness)
    result = cv2.cvtColor(cv2.merge((lightness, a, b)), cv2.COLOR_LAB2BGR)
    if is_dark(result):
        table = np.array([((i / 255) ** 0.6) * 255 for i in range(256)], dtype=np.uint8)
        result = cv2.LUT(result, table)
    return result  # type: ignore[no-any-return]


def enlarge(image: Image, min_width: int = 1280) -> Image:
    cv2 = _cv2()
    height, width = image.shape[:2]
    if width >= min_width:
        return image
    scale = min_width / width
    size = (round(width * scale), round(height * scale))
    return cv2.resize(image, size, interpolation=cv2.INTER_CUBIC)  # type: ignore[no-any-return]


def crop(image: Image, box: Box) -> Image:
    x, y, w, h = box
    height, width = image.shape[:2]
    x, y = max(0, x), max(0, y)
    w, h = min(w, width - x), min(h, height - y)
    return image[y : y + h, x : x + w].copy()


def region_box(width: int, height: int, region: str, factor: float = 2.0) -> Box:
    """Window of 1/factor of the picture centred on a 3x3 grid cell, kept inside the frame."""
    index = REGIONS.index(region)
    row, col = divmod(index, 3)
    w = max(MIN_CROP_WIDTH, round(width / factor))
    h = round(w * height / width)
    cx, cy = (col * 2 + 1) * width / 6, (row * 2 + 1) * height / 6
    x = int(min(max(cx - w / 2, 0), width - w))
    y = int(min(max(cy - h / 2, 0), height - h))
    return x, y, w, h


def zoom(image: Image, region: str) -> Image:
    height, width = image.shape[:2]
    return sharpen(enlarge(crop(image, region_box(width, height, region))), 0.5)


def around(box: Box, width: int, height: int, margin: float = 0.6) -> Box:
    """A box around a detection with room for the head and shoulders, in the frame's aspect."""
    x, y, w, h = box
    side = max(w, h) * (1 + 2 * margin)
    out_w = max(MIN_CROP_WIDTH, min(width, round(side * width / height)))
    out_h = min(height, round(out_w * height / width))
    cx, cy = x + w / 2, y + h / 2
    left = int(min(max(cx - out_w / 2, 0), width - out_w))
    top = int(min(max(cy - out_h / 2, 0), height - out_h))
    return left, top, out_w, out_h


class Locator(Protocol):
    def locate(self, image: Image) -> Box | None: ...


def best_view(series: list[Path], locator: Locator | None) -> tuple[Image, bool]:
    """Sharpest picture of a series, framed around a person if one is found."""
    image = load(sharpest(series))
    box = locator.locate(image) if locator else None
    if box is not None:
        height, width = image.shape[:2]
        image = enlarge(crop(image, around(box, width, height)))
    image = sharpen(image)
    if is_dark(image):
        image = brighten(image)
    return image, box is not None
