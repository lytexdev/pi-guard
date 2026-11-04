"""Things to do later, kept across restarts: "nimm heute Nacht von 3 bis 4 Uhr Ton auf"."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from pi_guard.audio.service import AudioError
from pi_guard.core.timeutil import utcnow
from pi_guard.messaging.base import Reply

if TYPE_CHECKING:
    from pi_guard.app import App

log = logging.getLogger(__name__)

MAX_RECORD_MINUTES = 120
SWEEP_SECONDS = 20
_TABLE = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, run_at TEXT NOT NULL, args TEXT NOT NULL);
"""


def next_time(text: str, tz: ZoneInfo, after: datetime | None = None) -> datetime:
    """The next time this clock time comes, in UTC."""
    hour, minute = (int(x) for x in text.split(":"))
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(text)
    base = (after or utcnow()).astimezone(tz)
    moment = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if moment <= base:
        moment += timedelta(days=1)
    return moment.astimezone(utcnow().tzinfo)


class Jobs:
    def __init__(self, app: App) -> None:
        self._app = app
        app.db.executescript(_TABLE)

    def add(self, kind: str, when: datetime, args: dict[str, Any]) -> None:
        self._app.db.execute("INSERT INTO jobs (kind, run_at, args) VALUES (?, ?, ?)",
                             (kind, when.isoformat(), json.dumps(args)))  # fmt: skip

    def list(self) -> list[dict[str, Any]]:
        rows = self._app.db.execute("SELECT * FROM jobs ORDER BY run_at").fetchall()
        return [{**dict(r), "args": json.loads(r["args"])} for r in rows]

    def delete(self, job_id: int) -> bool:
        return self._app.db.execute("DELETE FROM jobs WHERE id = ?", (job_id,)).rowcount > 0

    async def _record(self, args: dict[str, Any]) -> None:
        app = self._app
        audio = app.audio
        if audio is None or not audio.settings.microphone:
            return
        chime = audio.sounds.path("chime")
        if audio.playback_enabled and chime:  # a microphone is always announced
            try:
                await audio.play(chime, ignore_quiet_hours=True)
            except AudioError:
                log.info("could not announce the microphone")
        seconds = int(args["minutes"]) * 60
        out = app.media_dir / "audio" / f"planned_{utcnow():%Y%m%dT%H%M%S}.m4a"
        path = await audio.record(audio.settings.microphone, seconds, out, limit=seconds)
        provider, chat = args.get("provider"), args.get("chat")
        if provider and chat:
            await app.messenger.send(provider, chat, Reply("🎙", audio=path))

    async def run(self) -> None:
        while True:
            now = utcnow().isoformat()
            rows = self._app.db.execute(
                "SELECT * FROM jobs WHERE run_at <= ? ORDER BY run_at", (now,)
            ).fetchall()
            for row in rows:
                self._app.db.execute("DELETE FROM jobs WHERE id = ?", (row["id"],))
                try:
                    if row["kind"] == "record":
                        asyncio.get_running_loop().create_task(
                            self._record(json.loads(row["args"]))
                        )
                except (ValueError, KeyError):
                    log.warning("planned job could not run")
            await asyncio.sleep(SWEEP_SECONDS)
