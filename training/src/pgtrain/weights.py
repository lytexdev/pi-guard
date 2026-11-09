"""Models this computer needs, each checked against a fixed SHA-256 before use.

The Pi's three ONNX models come from pi-guard's own list. For training, the official
YOLOX PyTorch weights (Apache-2.0) of release 0.1.1rc0: Nano to start from, L to learn from.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import httpx
from pi_guard.detection.models import MODELS, Model, download

ATTEMPTS = 4

_YOLOX = "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0"
NANO = Model("yolox-nano-pth", "yolox_nano.pth", f"{_YOLOX}/yolox_nano.pth",
             "cd28f55fbbc1829f99d9ac9b38a16d259a22889739c8728ea877610201feff7b", 7.7,
             "Apache-2.0", "starting point for training the Pi's person detector")  # fmt: skip
LARGE = Model("yolox-l-pth", "yolox_l.pth", f"{_YOLOX}/yolox_l.pth",
              "1e6b7fa6240375370b2a8a8eab9066b3cdd43fd1d0bfa8d2027fd3a51def2917", 434.4,
              "Apache-2.0", "the bigger detector the Pi's one learns from")  # fmt: skip


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _ensure(model: Model, folder: Path) -> Path:
    path = model.path(folder)
    if path.is_file() and sha256(path) == model.sha256:
        return path
    print(f"downloading {model.filename} ({model.size_mb} MB, {model.license}) from {model.url}")
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return download(model, folder, timeout=1800)
        except httpx.HTTPError as exc:  # GitHub answers 504 now and then
            if attempt == ATTEMPTS:
                raise
            print(f"  failed ({type(exc).__name__}), trying again in {attempt * 10} s")
            time.sleep(attempt * 10)
    raise AssertionError("unreachable")


def ensure_faces(folder: Path) -> Path:
    for model in MODELS:
        _ensure(model, folder)
    return folder


def ensure_training(folder: Path) -> dict[str, Path]:
    return {"nano": _ensure(NANO, folder), "l": _ensure(LARGE, folder)}


def ensure_all(folder: Path) -> list[Path]:
    ensure_faces(folder)
    return [*(m.path(folder) for m in MODELS), *ensure_training(folder).values()]
