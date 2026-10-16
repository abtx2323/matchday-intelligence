"""Small in-memory abuse controls: per-IP rate limiting and stream slot accounting.

Single-process, single-event-loop structures (no awaits inside critical sections), suitable for
one replica; with several replicas each enforces its own share.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable

from starlette.requests import Request


def client_ip(request: Request, trusted_proxy_hops: int = 0) -> str:
    """Client address. With N trusted proxies, use the Nth entry from the right of X-Forwarded-For."""
    direct = request.client.host if request.client else "unknown"
    if trusted_proxy_hops <= 0:
        return direct
    chain = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    if len(chain) >= trusted_proxy_hops:
        return chain[-trusted_proxy_hops][:64]
    return direct


class RateLimiter:
    """Sliding-window limiter: at most ``limit`` requests per ``window_s`` per key."""

    def __init__(
        self, limit: int, window_s: float = 60.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = self._clock()
        q = self._hits[key]
        while q and now - q[0] >= self.window_s:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        if len(self._hits) > 10_000:  # bound memory: drop idle keys
            for k in [k for k, v in self._hits.items() if not v]:
                del self._hits[k]
        return True


class StreamSlots:
    """Global and per-IP caps on concurrent live streams."""

    def __init__(self, max_total: int, max_per_ip: int) -> None:
        self.max_total = max_total
        self.max_per_ip = max_per_ip
        self.total = 0
        self.per_ip: dict[str, int] = defaultdict(int)

    def available(self, ip: str) -> bool:
        return self.total < self.max_total and self.per_ip[ip] < self.max_per_ip

    def try_acquire(self, ip: str) -> bool:
        if not self.available(ip):
            return False
        self.total += 1
        self.per_ip[ip] += 1
        return True

    def release(self, ip: str) -> None:
        self.total = max(0, self.total - 1)
        self.per_ip[ip] = max(0, self.per_ip[ip] - 1)
        if not self.per_ip[ip]:
            del self.per_ip[ip]
