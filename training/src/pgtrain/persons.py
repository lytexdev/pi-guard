"""Teaching YOLOX-Nano, the Pi's person detector, from the much bigger YOLOX-L.

Distillation: both networks see the same picture, and the small one learns to give the
big one's answer in every grid cell (objectness, classes, boxes). No picture has to be
labelled by hand, so any folder of pictures from real homes can teach the Pi's view. The
student keeps all 80 classes and the Pi's output layout, so the exported file replaces
yolox_nano.onnx without any change on the Pi.

`evaluate` judges a model the way the Pi uses it: pi-guard's own code finds the people,
and YOLOX-L at 640 pixels is the reference.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import shutil
import tempfile
import time
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch
import torch.distributed as dist
import torch.multiprocessing as mp
import torch.nn.functional as F
from torch import nn
from torch.utils.data import DataLoader, Dataset, DistributedSampler

from pgtrain.faces import images
from pgtrain.yolox_model import YOLOX, Exported, load

log = logging.getLogger(__name__)

SIZE = 416  # what the Pi feeds the model
PAD = 114
VAL_SHARE = 0.1
PERSON = 0


def split(paths: list[Path]) -> tuple[list[Path], list[Path]]:
    """A fixed share for checking, chosen by name so it stays the same between runs."""

    def checking(p: Path) -> bool:
        return int(hashlib.sha256(p.name.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF < VAL_SHARE

    return [p for p in paths if not checking(p)], [p for p in paths if checking(p)]


def letterbox(image: np.ndarray, size: int = SIZE) -> tuple[np.ndarray, float]:
    """As the Pi does it: scaled to fit, top left, the rest grey."""
    height, width = image.shape[:2]
    ratio = min(size / height, size / width)
    resized = cv2.resize(image, (round(width * ratio), round(height * ratio)))
    out = np.full((size, size, 3), PAD, np.uint8)
    out[: resized.shape[0], : resized.shape[1]] = resized
    return out, ratio


def augment(image: np.ndarray, rng: random.Random) -> np.ndarray:
    """Varied views of one picture: size, place, mirror, light and colour."""
    height, width = image.shape[:2]
    scale = min(SIZE / height, SIZE / width) * rng.uniform(0.6, 1.4)
    w, h = max(8, round(width * scale)), max(8, round(height * scale))
    resized = cv2.resize(image, (w, h))
    if rng.random() < 0.5:
        resized = resized[:, ::-1]
    out = np.full((SIZE, SIZE, 3), PAD, np.uint8)
    x, y = (
        rng.randint(min(0, SIZE - w), max(0, SIZE - w)),
        rng.randint(min(0, SIZE - h), max(0, SIZE - h)),
    )
    src = resized[
        max(0, -y) : max(0, -y) + SIZE - max(0, y), max(0, -x) : max(0, -x) + SIZE - max(0, x)
    ]
    out[max(0, y) : max(0, y) + src.shape[0], max(0, x) : max(0, x) + src.shape[1]] = src
    hsv = cv2.cvtColor(out, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[..., 0] = (hsv[..., 0] + rng.uniform(-5, 5)) % 180
    hsv[..., 1:] *= [rng.uniform(0.7, 1.3), rng.uniform(0.5, 1.4)]  # darker rooms, too
    return cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR)


class Pictures(Dataset[torch.Tensor]):
    def __init__(self, paths: list[Path], train: bool, seed: int = 0) -> None:
        self.paths, self.train, self.seed = paths, train, seed
        self.epoch = 0

    def __len__(self) -> int:
        return len(self.paths)

    def __getitem__(self, index: int) -> torch.Tensor:
        image = cv2.imread(str(self.paths[index]))
        if image is None:
            image = np.full((SIZE, SIZE, 3), PAD, np.uint8)
        if self.train:
            rng = random.Random(hash((self.seed, self.epoch, index)))  # noqa: S311 not for secrets
            image = augment(image, rng)
        else:
            image, _ = letterbox(image)
        return torch.from_numpy(np.ascontiguousarray(image.transpose(2, 0, 1))).float()


def distill_loss(student: torch.Tensor, teacher: torch.Tensor) -> dict[str, torch.Tensor]:
    """student: raw logits; teacher: the Pi's output (sigmoid scores). (batch, cells, 85)"""
    s_obj, t_obj = student[..., 4], teacher[..., 4]
    obj = F.binary_cross_entropy_with_logits(s_obj, t_obj)
    weight = t_obj.detach()
    total = weight.sum().clamp(min=1.0)
    cls_all = F.binary_cross_entropy_with_logits(
        student[..., 5:], teacher[..., 5:], reduction="none"
    )
    cls = (cls_all.mean(-1) * weight).sum() / total
    person = (cls_all[..., PERSON] * weight).sum() / total  # what the Pi asks for counts double
    sure = weight * (weight > 0.3)
    box = (F.l1_loss(student[..., :4], teacher[..., :4], reduction="none").sum(-1) * sure).sum()
    box = box / sure.sum().clamp(min=1.0)
    return {"obj": obj, "cls": cls, "person": person, "box": box,
            "total": 2.0 * obj + cls + person + 0.5 * box}  # fmt: skip


class EMA:
    """An average of the weights over the last steps: steadier than the last step alone."""

    def __init__(self, model: nn.Module, decay: float = 0.9995) -> None:
        self.model = deepcopy(model).eval()
        self.decay, self.updates = decay, 0
        for p in self.model.parameters():
            p.requires_grad_(False)

    @torch.no_grad()
    def update(self, model: nn.Module) -> None:
        self.updates += 1
        d = self.decay * (1 - math.exp(-self.updates / 2000))
        current = model.state_dict()
        for key, value in self.model.state_dict().items():
            if value.dtype.is_floating_point:
                value.mul_(d).add_(current[key].detach(), alpha=1 - d)
            else:
                value.copy_(current[key])


@dataclass
class TrainConfig:
    pictures: str
    out: str
    student: str
    teacher: str
    epochs: int = 10
    batch: int = 16  # per GPU
    lr: float = 2e-5  # gently: at 2e-4 Nano forgot more than it learned, see docs/training.md
    workers: int = 6
    devices: int = 1
    limit: int = 0  # use only this many pictures (0: all), for a quick try


def _setup(rank: int, world: int) -> None:
    if world > 1:
        os.environ.setdefault("MASTER_ADDR", "127.0.0.1")
        os.environ.setdefault("MASTER_PORT", "29517")
        dist.init_process_group("nccl", rank=rank, world_size=world)
    torch.cuda.set_device(rank)


def _worker(rank: int, cfg: TrainConfig) -> None:
    world = cfg.devices
    _setup(rank, world)
    device = torch.device("cuda", rank)
    torch.backends.cudnn.benchmark = True
    paths = images(Path(cfg.pictures))
    if cfg.limit:
        paths = paths[: cfg.limit]
    train_paths, val_paths = split(paths)
    train_set, val_set = Pictures(train_paths, True), Pictures(val_paths, False)
    sampler = DistributedSampler(train_set, world, rank, shuffle=True) if world > 1 else None
    loader = DataLoader(train_set, cfg.batch, shuffle=sampler is None, sampler=sampler,
                        num_workers=cfg.workers, pin_memory=True, drop_last=True,
                        persistent_workers=cfg.workers > 0)  # fmt: skip
    val_loader = DataLoader(val_set, cfg.batch, num_workers=cfg.workers)
    # In float32: YOLOX-L overflows to NaN in float16, and a GTX 1660 is no faster in it.
    teacher = load("l", cfg.teacher, str(device))
    student: nn.Module = load("nano", cfg.student, str(device)).train()
    ema = EMA(student)
    if world > 1:
        student = nn.parallel.DistributedDataParallel(student, device_ids=[rank])
    optimizer = torch.optim.AdamW(student.parameters(), lr=cfg.lr, weight_decay=5e-4)
    steps = max(1, cfg.epochs * len(loader))
    scheduler = torch.optim.lr_scheduler.OneCycleLR(
        optimizer, cfg.lr, total_steps=steps, pct_start=0.05
    )
    out = Path(cfg.out)
    if rank == 0:
        out.mkdir(parents=True, exist_ok=True)
        (out / "config.json").write_text(json.dumps(asdict(cfg), indent=2))
        print(f"{len(train_paths)} pictures to learn from, {len(val_paths)} to check, "
              f"{world} GPU(s), {len(loader)} steps per round", flush=True)  # fmt: skip
    best = math.inf
    for epoch in range(cfg.epochs):
        started = time.monotonic()
        train_set.epoch = epoch
        if sampler is not None:
            sampler.set_epoch(epoch)
        student.train()
        running, skipped = 0.0, 0
        for batch in loader:
            batch = batch.to(device, non_blocking=True)
            with torch.no_grad():
                target = teacher(batch)
            predicted = student(batch, logits=True)
            loss = distill_loss(predicted, target)["total"]
            if not _everywhere_finite(loss, world):  # never learn from a broken answer
                skipped += 1
                scheduler.step()
                continue
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            nn.utils.clip_grad_norm_(student.parameters(), 10.0)
            optimizer.step()
            scheduler.step()
            ema.update(student.module if world > 1 else student)
            running += float(loss.detach())
        if rank == 0:
            val = _validate(ema.model, teacher, val_loader, device)
            learned = max(1, len(loader) - skipped)
            line = {"round": epoch + 1, "train_loss": round(running / learned, 4),
                    "skipped": skipped,
                    **{k: round(v, 4) for k, v in val.items()},
                    "seconds": round(time.monotonic() - started)}  # fmt: skip
            print(json.dumps(line), flush=True)
            with (out / "progress.jsonl").open("a") as f:
                f.write(json.dumps(line) + "\n")
            torch.save({"model": ema.model.state_dict(), "round": epoch + 1}, out / "last.pth")
            if val["total"] < best:
                best = val["total"]
                shutil.copy(out / "last.pth", out / "best.pth")
        if world > 1:
            dist.barrier()
    if world > 1:
        dist.destroy_process_group()


def _everywhere_finite(loss: torch.Tensor, world: int) -> bool:
    """All GPUs must skip the same step, or the others wait for it for ever."""
    ok = torch.isfinite(loss.detach()).float()
    if world > 1:
        dist.all_reduce(ok, op=dist.ReduceOp.MIN)
    return bool(ok.item())


@torch.no_grad()
def _validate(model: nn.Module, teacher: nn.Module, loader: DataLoader[torch.Tensor],
              device: torch.device) -> dict[str, float]:  # fmt: skip
    sums: dict[str, float] = {}
    count = 0
    for batch in loader:
        batch = batch.to(device)
        target = teacher(batch)
        predicted = model(batch, logits=True)
        for key, value in distill_loss(predicted, target).items():
            sums[key] = sums.get(key, 0.0) + float(value) * len(batch)
        count += len(batch)
    return {f"check_{k}" if k != "total" else "total": v / max(1, count) for k, v in sums.items()}


def train(cfg: TrainConfig) -> None:
    if not torch.cuda.is_available():
        raise SystemExit("training needs a CUDA GPU")
    cfg.devices = min(cfg.devices, torch.cuda.device_count())
    if cfg.devices > 1:
        mp.spawn(_worker, args=(cfg,), nprocs=cfg.devices, join=True)
    else:
        _worker(0, cfg)


def export(checkpoint: Path, out: Path) -> Path:
    """An ONNX file in the Pi's format: 1x3x416x416 BGR in, 1x3549x85 out."""
    model = load("nano", str(checkpoint))
    dummy = torch.zeros(1, 3, SIZE, SIZE)
    out.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(Exported(model), (dummy,), str(out), input_names=["images"],
                      output_names=["output"], opset_version=11, dynamo=False)  # fmt: skip
    net = cv2.dnn.readNet(str(out))  # what the Pi will do with it
    net.setInput(dummy.numpy())
    shape = net.forward().shape
    if tuple(shape) != (1, 3549, 85):
        raise RuntimeError(f"unexpected output {shape}")
    return out


# --- judging a model as the Pi uses it ----------------------------------------------------


def _iou(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    w = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    h = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = w * h
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def match(
    reference: list[tuple[float, ...]], found: list[tuple[float, ...]], iou: float = 0.5
) -> int:
    """How many reference people a detector found, each found box used once."""
    free = list(found)
    hits = 0
    for ref in sorted(reference, key=lambda b: b[2] * b[3], reverse=True):
        best = max(range(len(free)), key=lambda i: _iou(ref, free[i]), default=None)
        if best is not None and _iou(ref, free[best]) >= iou:
            hits += 1
            free.pop(best)
    return hits


def score(rows: list[tuple[int, int, int]]) -> dict[str, Any]:
    """rows: (reference people, found boxes, matched) per picture."""
    refs = sum(r for r, _, _ in rows)
    found = sum(f for _, f, _ in rows)
    hits = sum(m for _, _, m in rows)
    with_people = [r for r in rows if r[0]]
    seen = sum(1 for r in with_people if r[1])
    return {
        "pictures": len(rows), "people": refs,
        "recall": round(hits / refs, 3) if refs else None,
        "precision": round(hits / found, 3) if found else None,
        # What decides an event on the Pi: was anyone found where somebody is?
        "pictures_with_people_found": round(seen / len(with_people), 3) if with_people else None,
        "pictures_without_people_found": sum(1 for r in rows if not r[0] and r[1]),
    }  # fmt: skip


@torch.no_grad()
def reference_people(teacher: YOLOX, image: np.ndarray, device: str) -> list[tuple[float, ...]]:
    from pi_guard.detection.persons import NMS_OVERLAP, PERSON_MIN_SCORE, decode_yolox

    padded, ratio = letterbox(image, 640)
    batch = torch.from_numpy(padded.transpose(2, 0, 1)).float()[None].to(device)
    out = teacher(batch)[0].float().cpu().numpy()
    boxes, scores = decode_yolox(out, ratio, size=640)
    if not boxes:
        return []
    keep = cv2.dnn.NMSBoxes(boxes, scores, PERSON_MIN_SCORE, NMS_OVERLAP)
    return [boxes[i] for i in np.ravel(keep)]


def evaluate(
    pictures: Path, teacher_weights: Path, models: list[Path], limit: int = 0
) -> dict[str, Any]:
    """Each ONNX model on the checking share of `pictures`, through pi-guard's own code."""
    from pi_guard.detection.persons import PersonDetector

    device = "cuda" if torch.cuda.is_available() else "cpu"
    teacher = load("l", str(teacher_weights), device)
    _, val_paths = split(images(pictures))
    if limit:
        val_paths = val_paths[:limit]
    detectors = {}
    workdirs = []
    for model in models:
        work = tempfile.TemporaryDirectory(prefix="pgtrain-eval-")
        workdirs.append(work)
        shutil.copy(model, Path(work.name) / "yolox_nano.onnx")
        detectors[str(model)] = PersonDetector(Path(work.name))
    rows: dict[str, list[tuple[int, int, int]]] = {name: [] for name in detectors}
    for path in val_paths:
        image = cv2.imread(str(path))
        if image is None:
            continue
        reference = reference_people(teacher, image, device)
        for name, detector in detectors.items():
            found = detector.bodies(image)
            rows[name].append((len(reference), len(found), match(reference, found)))
    return {name: score(r) for name, r in rows.items()}
