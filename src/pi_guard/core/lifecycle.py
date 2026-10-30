"""Supervision of long-running components.

Each component runs as its own task. When one crashes it is restarted with
exponential backoff; the others keep running. This is what keeps recording
alive while Telegram, Signal or MQTT are failing.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class Backoff:
    initial: float = 2.0
    maximum: float = 300.0
    _current: float = field(init=False, default=0.0)

    def next(self) -> float:
        self._current = min(self.maximum, self._current * 2 if self._current else self.initial)
        return self._current

    def reset(self) -> None:
        self._current = 0.0


@dataclass
class ComponentState:
    name: str
    running: bool = False
    restarts: int = 0
    last_error: str | None = None


async def supervise(
    state: ComponentState,
    factory: Callable[[], Awaitable[None]],
    backoff: Backoff | None = None,
    healthy_after: float = 60.0,
) -> None:
    backoff = backoff or Backoff()
    while True:
        started = time.monotonic()
        state.running = True
        try:
            await factory()
            log.warning("%s exited; restarting", state.name)
        except asyncio.CancelledError:
            state.running = False
            raise
        except Exception as exc:
            state.last_error = f"{type(exc).__name__}: {exc}"
            log.exception("%s crashed", state.name)
        state.running = False
        state.restarts += 1
        if time.monotonic() - started > healthy_after:
            backoff.reset()
        delay = backoff.next()
        log.info("restarting %s in %.0fs", state.name, delay)
        await asyncio.sleep(delay)
