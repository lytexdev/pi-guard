from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class RateLimiter:
    """Token bucket per key: `burst` requests at once, refilled at `per_minute`."""

    per_minute: float = 20
    burst: int = 8
    max_keys: int = 1000
    _buckets: dict[str, tuple[float, float]] = field(default_factory=dict)

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if len(self._buckets) > self.max_keys:
            # Buckets idle long enough to be full again carry no information.
            refill = self.burst * 60 / self.per_minute
            self._buckets = {k: v for k, v in self._buckets.items() if now - v[1] < refill}
        tokens, last = self._buckets.get(key, (float(self.burst), now))
        tokens = min(self.burst, tokens + (now - last) * self.per_minute / 60)
        if tokens < 1:
            self._buckets[key] = (tokens, now)
            return False
        self._buckets[key] = (tokens - 1, now)
        return True
