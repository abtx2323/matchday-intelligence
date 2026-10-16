"""Small in-memory abuse controls: per-IP rate limiting and stream slot accounting.

Single-process, single-event-loop structures (no awaits inside critical sections), suitable for
one replica; with several replicas each enforces its own share.
"""

from __future__ import annotations

import time
from collections import OrderedDict, deque
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
    """Sliding-window limiter: at most ``limit`` hits per ``window_s`` per key.

    Keys are kept in recency order, so stale keys (whose newest hit is older than the window)
    are evicted from the front in amortised O(1); the table also has a hard size cap.
    """

    def __init__(
        self,
        limit: int,
        window_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 50_000,
    ) -> None:
        self.limit = limit
        self.window_s = window_s
        self.max_keys = max_keys
        self._clock = clock
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._hits)

    def _evict(self, now: float) -> None:
        while self._hits:
            _key, q = next(iter(self._hits.items()))
            if q and now - q[-1] < self.window_s:
                break
            self._hits.popitem(last=False)
        while len(self._hits) > self.max_keys:
            self._hits.popitem(last=False)

    def allow(self, key: str) -> bool:
        now = self._clock()
        self._evict(now)
        q = self._hits.get(key)
        if q is None:
            q = deque()
            self._hits[key] = q
        else:
            self._hits.move_to_end(key)
        while q and now - q[0] >= self.window_s:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        if len(self._hits) > self.max_keys:
            self._hits.popitem(last=False)
        return True


class StreamSlots:
    """Global and per-IP caps on concurrent live streams."""

    def __init__(self, max_total: int, max_per_ip: int) -> None:
        self.max_total = max_total
        self.max_per_ip = max_per_ip
        self.total = 0
        self.per_ip: dict[str, int] = {}

    def available(self, ip: str) -> bool:
        return self.total < self.max_total and self.per_ip.get(ip, 0) < self.max_per_ip

    def try_acquire(self, ip: str) -> bool:
        if not self.available(ip):
            return False
        self.total += 1
        self.per_ip[ip] = self.per_ip.get(ip, 0) + 1
        return True

    def release(self, ip: str) -> None:
        self.total = max(0, self.total - 1)
        left = self.per_ip.get(ip, 0) - 1
        if left > 0:
            self.per_ip[ip] = left
        else:
            self.per_ip.pop(ip, None)
