"""A small in-process sliding-window rate limiter.

Good enough for a single server. With several app instances, move the counters to Redis.
"""

import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, status


class RateLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many messages, please wait a minute")
            hits.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
