"""Resilience layer around language providers: gating, cache, timeout, retry, circuit breaker,
spend budget, deterministic fault injection, and a guaranteed template fallback.

``SharedVoiceState`` (cache, breaker, budget) is built once per app and shared by every match
session, so limits apply app-wide rather than per request.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..limits import RateLimiter
from .base import LanguageProvider, ProviderError, VoiceRequest, VoiceResult
from .template import TemplateProvider

log = logging.getLogger(__name__)


class CircuitBreaker:
    def __init__(
        self, threshold: int, cooldown_s: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self._clock = clock
        self.failures = 0
        self.opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self.opened_at is None:
            return False
        if self._clock() - self.opened_at >= self.cooldown_s:
            # Half-open: let the next call probe the provider.
            self.opened_at = None
            self.failures = self.threshold - 1
            return False
        return True

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def record_failure(self) -> bool:
        """Returns True when this failure opened the circuit."""
        self.failures += 1
        if self.failures >= self.threshold and self.opened_at is None:
            self.opened_at = self._clock()
            return True
        return False


class ModelBudget:
    """Global cap on model calls: a lifetime total and a rolling one-hour window."""

    def __init__(
        self, max_total: int, max_per_hour: int, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.max_total = max_total
        self.max_per_hour = max_per_hour
        self._clock = clock
        self.used_total = 0
        self._recent: deque[float] = deque()

    def try_acquire(self) -> bool:
        now = self._clock()
        while self._recent and now - self._recent[0] >= 3600:
            self._recent.popleft()
        if self.used_total >= self.max_total or len(self._recent) >= self.max_per_hour:
            return False
        self.used_total += 1
        self._recent.append(now)
        return True


class LRUCache:
    def __init__(self, size: int = 4096) -> None:
        self.size = size
        self._data: OrderedDict[str, str] = OrderedDict()

    def get(self, key: str) -> str | None:
        if key in self._data:
            self._data.move_to_end(key)
            return self._data[key]
        return None

    def put(self, key: str, value: str) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        if len(self._data) > self.size:
            self._data.popitem(last=False)


@dataclass
class SharedVoiceState:
    breaker: CircuitBreaker
    budget: ModelBudget
    cache: LRUCache = field(default_factory=LRUCache)
    per_client: RateLimiter | None = None  # each client's hourly share of model calls


class FaultInjector:
    """Seeded, reproducible fault injection used for demos and tests of failure recovery."""

    def __init__(self, rate: float, seed: int) -> None:
        self.rate = max(0.0, min(1.0, rate))
        self._rng = random.Random(seed * 7919 + 17)

    def maybe_fail(self, where: str) -> None:
        if self.rate and self._rng.random() < self.rate:
            raise ProviderError(f"injected fault in {where}")


def error_label(exc: BaseException) -> str:
    """Generic, client-safe label. Details are logged server-side only."""
    if isinstance(exc, TimeoutError | asyncio.TimeoutError):
        return "timeout"
    return "provider_error"


class VoiceService:
    def __init__(
        self,
        primary: LanguageProvider | None,
        *,
        timeout_s: float = 6.0,
        retries: int = 1,
        min_significance: float = 0.7,
        breaker: CircuitBreaker | None = None,
        budget: ModelBudget | None = None,
        cache: LRUCache | None = None,
        faults: FaultInjector | None = None,
        client_limiter: RateLimiter | None = None,
        client_key: str = "anonymous",
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.fallback = TemplateProvider()
        self.primary = primary
        self.timeout_s = timeout_s
        self.retries = retries
        self.min_significance = min_significance
        self.breaker = breaker or CircuitBreaker(3, 30.0)
        self.budget = budget
        self.cache = cache or LRUCache(2048)
        self.faults = faults
        self.client_limiter = client_limiter
        self.client_key = client_key
        self.on_event = on_event
        self.stats = {
            "calls": 0,
            "primary_ok": 0,
            "fallbacks": 0,
            "retries": 0,
            "circuit_opens": 0,
            "gated": 0,
            "cached": 0,
            "budget_exhausted": 0,
        }

    @property
    def primary_name(self) -> str:
        return self.primary.name if self.primary else self.fallback.name

    def _emit(self, **kw: Any) -> None:
        if self.on_event:
            self.on_event(kw)

    def _fallback(self, req: VoiceRequest, status: str, **kw: Any) -> VoiceResult:
        self.stats["fallbacks"] += 1
        return VoiceResult(
            req.fallback_text, f"{self.fallback.name}(fallback)", fallback_used=True, status=status, **kw
        )

    async def voice(self, req: VoiceRequest) -> VoiceResult:
        self.stats["calls"] += 1
        if self.primary is None or req.significance < self.min_significance:
            self.stats["gated"] += 1
            return VoiceResult(req.fallback_text, self.fallback.name, status="gated")
        if req.cache_key:
            hit = self.cache.get(req.cache_key)
            if hit is not None:
                self.stats["cached"] += 1
                return VoiceResult(hit, self.primary.name, status="cached")
        if self.breaker.is_open:
            self._emit(status="circuit_open", provider=self.primary.name, key=req.key)
            return self._fallback(req, "circuit_open")

        errors: list[str] = []
        start = time.perf_counter()
        for attempt in range(1, self.retries + 2):
            try:
                # Injected (demo) faults are checked first so they never consume budget.
                if self.faults:
                    self.faults.maybe_fail(f"{self.primary.name}:{req.task}")
                if (self.client_limiter is not None and not self.client_limiter.allow(self.client_key)) or (
                    self.budget is not None and not self.budget.try_acquire()
                ):
                    self.stats["budget_exhausted"] += 1
                    self._emit(status="budget_exhausted", provider=self.primary.name, key=req.key)
                    return self._fallback(req, "budget_exhausted", errors=errors)
                text = await asyncio.wait_for(self.primary.voice(req), self.timeout_s)
                self.breaker.record_success()
                self.stats["primary_ok"] += 1
                if req.cache_key:
                    self.cache.put(req.cache_key, text)
                if attempt > 1:
                    self._emit(
                        status="retried",
                        provider=self.primary.name,
                        key=req.key,
                        attempts=attempt,
                        errors=errors,
                    )
                return VoiceResult(
                    text,
                    self.primary.name,
                    attempts=attempt,
                    latency_ms=(time.perf_counter() - start) * 1000,
                    errors=errors,
                    status="retried" if attempt > 1 else "ok",
                )
            except Exception as exc:  # noqa: BLE001 - any provider failure must degrade safely
                errors.append(error_label(exc))
                log.warning("voice provider %s failed (%s): %r", self.primary.name, req.key, exc)
            if attempt <= self.retries:
                self.stats["retries"] += 1
                await asyncio.sleep(min(0.05 * attempt, 0.2))

        opened = self.breaker.record_failure()
        if opened:
            self.stats["circuit_opens"] += 1
        self._emit(
            status="fallback",
            provider=self.primary.name,
            key=req.key,
            attempts=self.retries + 1,
            errors=errors,
            circuit_opened=opened,
        )
        return self._fallback(
            req,
            "fallback",
            attempts=self.retries + 1,
            latency_ms=(time.perf_counter() - start) * 1000,
            errors=errors,
        )
