"""Anonymous numbers an owner may choose to send, to make detection better for everybody.

Off by default. Once a week, when switched on and a receiver is set (`support_url`), the Pi
sends one report: counts and histograms from the last seven days. Never pictures, face
prints, names, times of day, addresses or anything that tells installations apart. The
dashboard shows the exact report before it is switched on.

What it is for: the face size and score histograms, and above all the scores of faces an
admin confirmed ("this is X") or rejected ("this is not X"), show where the thresholds in
FaceSettings should be. `training/` reads the reports, see docs/training.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx

from pi_guard import __version__
from pi_guard.core.timeutil import utcnow

if TYPE_CHECKING:
    from pi_guard.app import App

log = logging.getLogger(__name__)

REPORT_VERSION = 1
ENABLED_KEY = "contribution.enabled"
SENT_KEY = "contribution.last_sent"
CORRECTIONS_KEY = "contribution.corrections"
EVERY = timedelta(days=7)
CHECK_SECONDS = 3600
PX_BIN, PX_BINS = 8, 21  # face sizes 0-7, 8-15, ... 160 and more
SCORE_BINS = 20  # similarity 0.00-0.05, ... 0.95-1.00; below 0 counts as 0
PERSON_TYPES = ("person_detected", "unknown_person", "known_person")


def _bucket(value: float, size: float, bins: int) -> int:
    return min(bins - 1, max(0, int(value / size)))


def score_bin(score: float) -> int:
    return _bucket(score, 1 / SCORE_BINS, SCORE_BINS)


def _histogram(values: list[float], size: float, bins: int) -> list[int]:
    counts = [0] * bins
    for value in values:
        counts[_bucket(value, size, bins)] += 1
    return counts


def _few(count: int) -> str:
    """Small numbers as they are, larger ones as "5+": fewer ways to tell homes apart."""
    return str(count) if count < 5 else "5+"


def record_correction(app: App, confirmed: bool, score: float) -> None:
    """An admin said a face is (or is not) a person; keep only the score's bin."""
    saved = app.db.get(CORRECTIONS_KEY) or {}
    key = "is" if confirmed else "not"
    counts = saved.get(key) or [0] * SCORE_BINS
    counts[score_bin(score)] += 1
    app.db.set(CORRECTIONS_KEY, {**saved, key: counts})


def build_report(app: App) -> dict[str, Any]:
    now = utcnow()
    since = (now - EVERY).isoformat()
    rows = app.db.execute("SELECT type, data FROM events WHERE ts >= ?", (since,)).fetchall()
    types: Counter[str] = Counter()
    face_px: list[float] = []
    scores: dict[str, list[float]] = {t: [] for t in PERSON_TYPES}
    bodies: Counter[str] = Counter()
    pictures: Counter[str] = Counter()
    detectors: Counter[str] = Counter()
    for row in rows:
        types[row["type"]] += 1
        if row["type"] not in PERSON_TYPES:
            continue
        data = json.loads(row["data"])
        face_px += [float(v) for v in data.get("face_px", [])]
        scores[row["type"]] += [float(v) for v in data.get("scores", [])]
        bodies[_few(int(data.get("bodies", 0)))] += 1
        pictures[str(data.get("pictures", 1))] += 1
        detectors[str(data.get("detector", "hog"))] += 1
    corrections = app.db.get(CORRECTIONS_KEY) or {}
    faces = app.faces
    year, week, _ = now.isocalendar()
    return {
        "report": REPORT_VERSION,
        "pi_guard": __version__,
        "week": f"{year}-W{week:02d}",
        "setup": {
            "cameras": _few(len(app.cameras)),
            "resolutions": sorted({c.resolution for c in app.config.cameras}),
            "recognition": faces is not None,
            "people": _few(len(faces.store.people())) if faces is not None else "0",
            "threshold": app.config.file.faces.match_threshold,
            "margin": app.config.file.faces.uncertain_margin,
        },
        "events": dict(sorted(types.items())),
        "face_px": _histogram(face_px, PX_BIN, PX_BINS),
        "scores": {t: _histogram(v, 1 / SCORE_BINS, SCORE_BINS) for t, v in scores.items()},
        "bodies": dict(sorted(bodies.items())),
        "pictures": dict(sorted(pictures.items())),
        "detectors": dict(sorted(detectors.items())),
        "corrections": {key: corrections.get(key) or [0] * SCORE_BINS for key in ("is", "not")},
    }


class Contribution:
    def __init__(self, app: App) -> None:
        self._app = app

    @property
    def url(self) -> str | None:
        return self._app.settings.support_url

    @property
    def enabled(self) -> bool:
        return bool(self._app.db.get(ENABLED_KEY))

    def last_sent(self) -> str | None:
        value = self._app.db.get(SENT_KEY)
        return str(value) if value else None

    def due(self) -> bool:
        if not (self.enabled and self.url):
            return False
        last = self.last_sent()
        return last is None or utcnow() - _parse(last) >= EVERY

    async def send(self) -> None:
        assert self.url is not None
        report = await asyncio.to_thread(build_report, self._app)
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            response = await client.post(self.url, json=report,
                                         headers={"User-Agent": "pi-guard"})  # fmt: skip
            response.raise_for_status()
        self._app.db.set(SENT_KEY, utcnow().isoformat())
        self._app.db.set(CORRECTIONS_KEY, {})  # sent once, then forgotten
        log.info("anonymous statistics sent")

    async def run(self) -> None:
        while True:
            if self.due():
                try:
                    await self.send()
                except (httpx.HTTPError, OSError) as exc:
                    log.warning("anonymous statistics not sent: %s", type(exc).__name__)
            await asyncio.sleep(CHECK_SECONDS)


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)
