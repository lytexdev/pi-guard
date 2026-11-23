"""The assistant's look at something pi-guard collected: the security check or the logs.

A review runs in the background, so leaving the page does not lose it, and its stage is real:
`prepare` while the input is built, `ask` while the AI is answering, `check` while the answer
is checked. The last result of each kind is kept until the next one.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pi_guard.core.timeutil import utcnow

log = logging.getLogger(__name__)

KINDS = ("scan", "logs")


@dataclass
class _State:
    stage: str
    started: float
    task: asyncio.Task[None] | None = None
    error: str | None = None


class Reviews:
    def __init__(self, db: Any) -> None:
        self._db = db
        self._states: dict[str, _State] = {}

    def last(self, kind: str) -> dict[str, Any] | None:
        result: dict[str, Any] | None = self._db.get(f"review.{kind}")
        return result

    def state(self, kind: str) -> dict[str, Any]:
        s = self._states.get(kind)
        running = s is not None and s.task is not None and not s.task.done()
        return {
            "running": running,
            "stage": s.stage if running and s else None,
            "seconds": round(time.monotonic() - s.started) if running and s else None,
            "error": None if running or s is None else s.error,
            "last": self.last(kind),
        }

    def start(
        self,
        kind: str,
        prepare: Callable[[], Awaitable[tuple[str, str]]],
        ask: Callable[[str, str], Awaitable[str]],
        parse: Callable[[str], dict[str, Any]],
        basis: str | None = None,
    ) -> bool:
        """`prepare` gives (system prompt, input), `ask` the AI's answer, `parse` the result.
        False when a review of this kind is already running."""
        current = self._states.get(kind)
        if current is not None and current.task is not None and not current.task.done():
            return False
        state = _State("prepare", time.monotonic())
        self._states[kind] = state

        async def work() -> None:
            from pi_guard.addons.assistant import AssistantError

            try:
                system, text = await prepare()
                state.stage = "ask"
                answer = await ask(system, text)
                state.stage = "check"
                result = parse(answer)
                self._db.set(f"review.{kind}", {**result, "time": utcnow().isoformat(),
                                                "basis": basis})  # fmt: skip
            except AssistantError as exc:
                state.error = exc.key
            except Exception:
                log.exception("%s review failed", kind)
                state.error = "web.error"

        state.task = asyncio.create_task(work(), name=f"review-{kind}")
        return True
