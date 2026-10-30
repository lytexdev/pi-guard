"""Optional ONNX models from the OpenCV Model Zoo, downloaded only on request.

Nothing is downloaded automatically. `pi-guard models download` shows what will
be fetched, from where, and how large it is, and verifies SHA-256 checksums.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import httpx

from pi_guard.security.files import atomic_write

_ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models"


@dataclass(frozen=True)
class Model:
    name: str
    filename: str
    url: str
    sha256: str
    size_mb: float
    license: str
    purpose: str

    def path(self, models_dir: Path) -> Path:
        return models_dir / self.filename

    def present(self, models_dir: Path) -> bool:
        return self.path(models_dir).is_file()


YUNET = Model(
    "yunet",
    "face_detection_yunet_2023mar.onnx",
    f"{_ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    0.2,
    "MIT",
    "face detection (needed for person detection by face and for recognition)",
)
SFACE = Model(
    "sface",
    "face_recognition_sface_2021dec.onnx",
    f"{_ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
    "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    36.9,
    "Apache-2.0",
    "face recognition embeddings (only for enrolled trusted people)",
)
MODELS = (YUNET, SFACE)


class ChecksumError(Exception):
    pass


def download(model: Model, models_dir: Path, timeout: float = 300) -> Path:
    models_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    chunks = []
    with httpx.stream("GET", model.url, follow_redirects=True, timeout=timeout) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            digest.update(chunk)
            chunks.append(chunk)
    if digest.hexdigest() != model.sha256:
        raise ChecksumError(f"{model.filename}: checksum mismatch, file discarded")
    path = model.path(models_dir)
    atomic_write(path, b"".join(chunks), mode=0o644)
    return path
