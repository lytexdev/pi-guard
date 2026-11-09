"""Receiving the anonymous statistics of Pis whose owners switched them on, and adding them up.

The receiver stores each report as one JSON file per week. It keeps no address, no time
finer than the week the Pi put in, and nothing a report did not contain. Anything that is
not a well-formed report is refused, so a stranger cannot fill the disk with other data.
"""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from aiohttp import web
from pi_guard.diagnostics.contribution import PX_BINS, REPORT_VERSION, SCORE_BINS

MAX_BYTES = 16 * 1024
WEEK = re.compile(r"^\d{4}-W\d{2}$")
SMALL = re.compile(r"^(\d|5\+)$")
MAX_COUNT = 1_000_000


class BadReport(ValueError):
    pass


def _counts(value: Any, length: int) -> list[int]:
    if not (isinstance(value, list) and len(value) == length):
        raise BadReport("histogram of the wrong length")
    if not all(isinstance(v, int) and 0 <= v <= MAX_COUNT for v in value):
        raise BadReport("counts must be small whole numbers")
    return value


def _tally(value: Any, keys: re.Pattern[str]) -> dict[str, int]:
    if not isinstance(value, dict) or len(value) > 20:
        raise BadReport("expected a few counts")
    for key, count in value.items():
        if not (isinstance(key, str) and keys.match(key)):
            raise BadReport(f"unexpected key {key!r}")
        if not (isinstance(count, int) and 0 <= count <= MAX_COUNT):
            raise BadReport("counts must be small whole numbers")
    return value


def check(report: Any) -> dict[str, Any]:
    """The report with only the fields of version 1, or BadReport."""
    if not isinstance(report, dict) or report.get("report") != REPORT_VERSION:
        raise BadReport("not a pi-guard report of a known version")
    if not (isinstance(report.get("week"), str) and WEEK.match(report["week"])):
        raise BadReport("week must look like 2026-W46")
    version = report.get("pi_guard")
    if not (isinstance(version, str) and re.match(r"^[0-9A-Za-z.+-]{1,32}$", version)):
        raise BadReport("bad version")
    setup = report.get("setup")
    if not isinstance(setup, dict):
        raise BadReport("setup missing")
    clean_setup: dict[str, Any] = {
        "cameras": str(setup.get("cameras", "")),
        "resolutions": [r for r in setup.get("resolutions", [])
                        if isinstance(r, str) and re.match(r"^\d{2,5}x\d{2,5}$", r)][:8],
        "recognition": bool(setup.get("recognition")),
        "people": str(setup.get("people", "")),
        "threshold": float(setup.get("threshold", 0)),
        "margin": float(setup.get("margin", 0)),
    }  # fmt: skip
    if not (SMALL.match(clean_setup["cameras"]) and SMALL.match(clean_setup["people"])):
        raise BadReport("setup counts must be 0-4 or 5+")
    scores = report.get("scores")
    corrections = report.get("corrections")
    if not isinstance(scores, dict) or not isinstance(corrections, dict):
        raise BadReport("scores missing")
    return {
        "report": REPORT_VERSION, "pi_guard": version, "week": report["week"],
        "setup": clean_setup,
        "events": _tally(report.get("events", {}), re.compile(r"^[a-z_]{1,40}$")),
        "face_px": _counts(report.get("face_px"), PX_BINS),
        "scores": {t: _counts(scores.get(t), SCORE_BINS)
                   for t in ("person_detected", "unknown_person", "known_person")},
        "bodies": _tally(report.get("bodies", {}), SMALL),
        "pictures": _tally(report.get("pictures", {}), re.compile(r"^\d$")),
        "detectors": _tally(report.get("detectors", {}), re.compile(r"^(yolox|hog)$")),
        "corrections": {k: _counts(corrections.get(k), SCORE_BINS) for k in ("is", "not")},
    }  # fmt: skip


def receiver(folder: Path) -> web.Application:
    async def receive(request: web.Request) -> web.Response:
        if (request.content_length or 0) > MAX_BYTES:
            raise web.HTTPRequestEntityTooLarge(
                max_size=MAX_BYTES, actual_size=request.content_length or 0
            )
        body = await request.content.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise web.HTTPRequestEntityTooLarge(max_size=MAX_BYTES, actual_size=len(body))
        try:
            report = check(json.loads(body))
        except (ValueError, TypeError) as exc:
            raise web.HTTPBadRequest(text=str(exc)[:200]) from None
        week = folder / report["week"]
        week.mkdir(parents=True, exist_ok=True)
        (week / f"{uuid.uuid4().hex}.json").write_text(json.dumps(report))
        return web.Response(status=204)

    app = web.Application(client_max_size=MAX_BYTES)
    app.router.add_post("/v1/report", receive)
    return app


def load(folder: Path) -> list[dict[str, Any]]:
    reports = []
    for path in sorted(folder.glob("*/*.json")):
        try:
            reports.append(check(json.loads(path.read_text())))
        except (ValueError, TypeError):
            continue
    return reports


def summary(reports: list[dict[str, Any]]) -> dict[str, Any]:
    def add(lists: list[list[int]], length: int) -> list[int]:
        return [sum(values[i] for values in lists) for i in range(length)]

    def tally(key: str) -> dict[str, int]:
        total: Counter[str] = Counter()
        for r in reports:
            total.update(r[key])
        return dict(sorted(total.items()))

    face_px = add([r["face_px"] for r in reports], PX_BINS)
    faces = sum(face_px)
    learnable = sum(face_px[7:])  # 56 px and more
    return {
        "reports": len(reports),
        "weeks": sorted({r["week"] for r in reports}),
        "versions": dict(Counter(r["pi_guard"] for r in reports)),
        "events": tally("events"),
        "detectors": tally("detectors"),
        "face_px": face_px,
        "faces_56px_or_more": round(learnable / faces, 3) if faces else None,
        "scores": {t: add([r["scores"][t] for r in reports], SCORE_BINS)
                   for t in ("person_detected", "unknown_person", "known_person")},
        "corrections": {k: add([r["corrections"][k] for r in reports], SCORE_BINS)
                        for k in ("is", "not")},
    }  # fmt: skip
