"""Resilience layer around language providers: gating, cache, timeout, retry, circuit breaker,
deterministic fault injection, and a guaranteed template fallback."""

from __future__ import annotations

import asyncio
import random
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from .base import LanguageProvider, ProviderError, VoiceRequest, VoiceResult
from .template import TemplateProvider


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


class FaultInjector:
    """Seeded, reproducible fault injection used for demos and tests of failure recovery."""

    def __init__(self, rate: float, seed: int) -> None:
        self.rate = max(0.0, min(1.0, rate))
        self._rng = random.Random(seed * 7919 + 17)

    def maybe_fail(self, where: str) -> None:
        if self.rate and self._rng.random() < self.rate:
            raise ProviderError(f"injected fault in {where}")


class VoiceService:
    def __init__(
        self,
        primary: LanguageProvider | None,
        *,
        timeout_s: float = 6.0,
        retries: int = 1,
        min_significance: float = 0.7,
        breaker: CircuitBreaker | None = None,
        faults: FaultInjector | None = None,
        cache_size: int = 2048,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.fallback = TemplateProvider()
        self.primary = primary
        self.timeout_s = timeout_s
        self.retries = retries
        self.min_significance = min_significance
        self.breaker = breaker or CircuitBreaker(3, 30.0)
        self.faults = faults
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._cache_size = cache_size
        self.on_event = on_event
        self.stats = {
            "calls": 0,
            "primary_ok": 0,
            "fallbacks": 0,
            "retries": 0,
            "circuit_opens": 0,
            "gated": 0,
            "cached": 0,
        }

    @property
    def primary_name(self) -> str:
        return self.primary.name if self.primary else self.fallback.name

    def _emit(self, **kw: Any) -> None:
        if self.on_event:
            self.on_event(kw)

    async def voice(self, req: VoiceRequest) -> VoiceResult:
        self.stats["calls"] += 1
        if self.primary is None or req.significance < self.min_significance:
            self.stats["gated"] += 1
            return VoiceResult(req.fallback_text, self.fallback.name, status="gated")
        if req.cache_key and req.cache_key in self._cache:
            self.stats["cached"] += 1
            self._cache.move_to_end(req.cache_key)
            return VoiceResult(self._cache[req.cache_key], self.primary.name, status="cached")
        if self.breaker.is_open:
            self.stats["fallbacks"] += 1
            self._emit(status="circuit_open", provider=self.primary.name, key=req.key)
            return VoiceResult(
                req.fallback_text,
                f"{self.fallback.name}(fallback)",
                fallback_used=True,
                status="circuit_open",
            )

        errors: list[str] = []
        start = time.perf_counter()
        for attempt in range(1, self.retries + 2):
            try:
                if self.faults:
                    self.faults.maybe_fail(f"{self.primary.name}:{req.task}")
                text = await asyncio.wait_for(self.primary.voice(req), self.timeout_s)
                self.breaker.record_success()
                self.stats["primary_ok"] += 1
                if req.cache_key:
                    self._cache[req.cache_key] = text
                    if len(self._cache) > self._cache_size:
                        self._cache.popitem(last=False)
                latency = (time.perf_counter() - start) * 1000
                status = "retried" if attempt > 1 else "ok"
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
                    latency_ms=latency,
                    errors=errors,
                    status=status,
                )
            except TimeoutError:
                errors.append(f"timeout after {self.timeout_s}s")
            except Exception as exc:  # noqa: BLE001 - any provider failure must degrade safely
                errors.append(f"{type(exc).__name__}: {str(exc)[:160]}")
            if attempt <= self.retries:
                self.stats["retries"] += 1
                await asyncio.sleep(min(0.05 * attempt, 0.2))

        opened = self.breaker.record_failure()
        if opened:
            self.stats["circuit_opens"] += 1
        self.stats["fallbacks"] += 1
        self._emit(
            status="fallback",
            provider=self.primary.name,
            key=req.key,
            attempts=self.retries + 1,
            errors=errors,
            circuit_opened=opened,
        )
        return VoiceResult(
            req.fallback_text,
            f"{self.fallback.name}(fallback)",
            fallback_used=True,
            attempts=self.retries + 1,
            latency_ms=(time.perf_counter() - start) * 1000,
            errors=errors,
            status="fallback",
        )
