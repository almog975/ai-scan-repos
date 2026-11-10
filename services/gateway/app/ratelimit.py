"""In-process fixed-window rate limiter (per gateway replica) -> 429 RATE_LIMITED + Retry-After."""
from __future__ import annotations

import threading
import time


class RateLimiter:
    def __init__(self, per_minute: int, clock=time.monotonic):
        self.limit, self.clock = per_minute, clock
        self.win: dict[str, tuple[int, int]] = {}
        self.lock = threading.Lock()

    def hit(self, key: str) -> int | None:
        """None if allowed, else seconds until the window resets."""
        if self.limit <= 0:
            return None
        now = self.clock()
        window = int(now // 60)
        with self.lock:
            w, n = self.win.get(key, (window, 0))
            if w != window:
                w, n = window, 0
            n += 1
            self.win[key] = (w, n)
            if len(self.win) > 10_000:  # bound memory: drop stale windows
                self.win = {k: v for k, v in self.win.items() if v[0] == window}
        return None if n <= self.limit else max(1, int(60 - now % 60))
