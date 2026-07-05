import collections
import threading
import time
from typing import Deque, Iterable, Tuple


class RateLimiter:
    """Thread-safe rate limiter using a sliding time window."""

    def __init__(self, max_calls: int, period_seconds: float) -> None:
        if max_calls <= 0:
            raise ValueError('max_calls must be greater than zero')
        if period_seconds <= 0:
            raise ValueError('period_seconds must be greater than zero')

        self._max_calls = max_calls
        self._period_seconds = period_seconds
        self._timestamps: Deque[float] = collections.deque()
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        cutoff = now - self._period_seconds
        while self._timestamps and self._timestamps[0] <= cutoff:
            self._timestamps.popleft()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._prune(now)
                if len(self._timestamps) < self._max_calls:
                    self._timestamps.append(now)
                    return
                earliest = self._timestamps[0]
                sleep_time = earliest + self._period_seconds - now

            if sleep_time > 0:
                time.sleep(sleep_time)
            else:
                time.sleep(0)

    def __enter__(self) -> 'RateLimiter':
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        return None


DEFAULT_GROWW_QUOTE_RATE_LIMITS = [
    (10, 1.0),
    (300, 60.0),
]

DEFAULT_GROQ_RATE_LIMITS = [
    (25, 60.0),
]


class MultiRateLimiter:
    """Combine multiple rate limiters for layered API limits."""

    def __init__(self, limits: Iterable[Tuple[int, float]]) -> None:
        self._limiters = [RateLimiter(max_calls, period) for max_calls, period in limits]

    def acquire(self) -> None:
        for limiter in self._limiters:
            limiter.acquire()

    def __enter__(self) -> 'MultiRateLimiter':
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        return None
