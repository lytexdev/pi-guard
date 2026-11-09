from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from pi_guard.detection import persons
from pi_guard.detection.persons import (
    PersonDetector,
    decode_yolox,
    head_region,
    merge_faces,
    shift_faces,
)

cv2 = pytest.importorskip("cv2")


def _face(x: float, y: float, size: float, score: float = 0.9) -> np.ndarray:
    """A YuNet row: box, five landmarks inside it, score."""
    row = [x, y, size, size]
    for lx, ly in ((0.3, 0.4), (0.7, 0.4), (0.5, 0.6), (0.35, 0.8), (0.65, 0.8)):
        row += [x + lx * size, y + ly * size]
    return np.array([[*row, score]], dtype=np.float32)


def test_yolox_output_becomes_person_boxes() -> None:
    out = np.zeros((3549, 85), dtype=np.float32)  # 52² + 26² + 13² cells for 416 pixels
    cell = 5 * 52 + 10  # stride 8, column 10, row 5
    out[cell, :4] = [0.5, 0.5, np.log(4), np.log(4)]
    out[cell, 4:6] = [0.9, 0.9]
    out[cell + 1, 4:6] = [0.9, 0.3]  # too unsure
    out[cell + 2, 4] = 0.9
    out[cell + 2, 6] = 0.9  # sure, but of another class
    boxes, scores = decode_yolox(out, ratio=0.5)
    assert boxes == [pytest.approx((136.0, 56.0, 64.0, 64.0))]
    assert scores == [pytest.approx(0.81)]


def test_faces_found_in_a_crop_move_back_into_the_picture() -> None:
    shifted = shift_faces(_face(10, 20, 40), 100, 200)
    assert shifted[0, :4].tolist() == [110, 220, 40, 40]
    assert shifted[0, 4:6].tolist() == pytest.approx([122, 236])  # first landmark
    assert shifted[0, 14] == pytest.approx(0.9)  # the score stays


def test_head_region_is_the_upper_body_and_skips_tiny_people() -> None:
    assert head_region((600, 200, 100, 300), 1280, 720) == (585, 170, 715, 380)
    assert head_region((0, 0, 100, 300), 1280, 720)[:2] == (0, 0)  # kept inside the picture
    assert head_region((600, 200, 10, 30), 1280, 720) is None


def test_the_same_face_is_counted_once() -> None:
    crop = _face(595, 190, 40)
    whole = np.concatenate((_face(597, 192, 38), _face(100, 100, 30)))  # same face, another
    merged = merge_faces(whole, [crop])
    assert merged is not None and len(merged) == 2
    assert merged[0, 0] == 595  # the face from the crop wins
    assert merge_faces(None, []) is None


def test_without_yolox_hog_finds_bodies(tmp_path: Path) -> None:
    detector = PersonDetector(tmp_path, compare=True)
    assert detector.body_detector == "hog"
    assert not detector.compare  # nothing to compare with
    photo = tmp_path / "empty.jpg"
    cv2.imwrite(str(photo), np.full((720, 1280, 3), 90, np.uint8))
    result = detector.detect(photo)
    assert (result.bodies, result.detector, result.hog_bodies) == (0, "hog", None)


def test_a_small_face_is_found_in_the_head_of_a_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On the 640 copy the face is too small; in the crop at full size it is found."""
    detector = PersonDetector(tmp_path)
    detector._faces = object()
    monkeypatch.setattr(detector, "bodies", lambda image: [(600.0, 200.0, 100.0, 300.0)])
    seen = []

    def faces(image: np.ndarray) -> np.ndarray | None:
        seen.append(image.shape[:2])
        return _face(10, 20, 40) if image.shape[1] < persons.ANALYSIS_WIDTH else None

    monkeypatch.setattr(detector, "faces", faces)
    photo = tmp_path / "far.jpg"
    cv2.imwrite(str(photo), np.full((720, 1280, 3), 90, np.uint8))
    result = detector.detect(photo)
    assert seen == [(360, 640), (210, 130)]  # the whole copy, then the head
    assert result.face_count == 1
    assert result.faces is not None and result.faces[0, :4].tolist() == [595, 190, 40, 40]
