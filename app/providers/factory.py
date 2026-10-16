from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ..config import VALID_PROVIDERS, Settings
from ..limits import RateLimiter
from .base import LanguageProvider, ProviderError
from .resilient import CircuitBreaker, FaultInjector, LRUCache, ModelBudget, SharedVoiceState, VoiceService
from .template import TemplateProvider

log = logging.getLogger(__name__)


def build_primary(settings: Settings) -> LanguageProvider | None:
    """Return the configured cloud provider, or None for pure offline template mode.

    Raises ``ProviderError`` for an unknown provider or incomplete cloud configuration so that
    misconfiguration fails at startup instead of silently at request time.
    """
    if settings.provider not in VALID_PROVIDERS:
        raise ProviderError("MDI_PROVIDER must be one of: " + ", ".join(VALID_PROVIDERS))
    if settings.provider == "template":
        return None
    from .agent_framework_provider import AgentFrameworkProvider

    provider = AgentFrameworkProvider(settings.provider, settings)
    provider.validate()
    return provider


def build_shared_state(settings: Settings) -> SharedVoiceState:
    return SharedVoiceState(
        breaker=CircuitBreaker(settings.breaker_threshold, settings.breaker_cooldown_s),
        budget=ModelBudget(settings.llm_max_calls_total, settings.llm_max_calls_per_hour),
        cache=LRUCache(4096),
        per_client=RateLimiter(settings.llm_calls_per_client_per_hour, 3600.0),
    )


def build_voice_service(
    settings: Settings,
    *,
    seed: int = 0,
    fault_rate: float = 0.0,
    primary: LanguageProvider | None = None,
    shared: SharedVoiceState | None = None,
    client_key: str = "anonymous",
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> VoiceService:
    """Per-session voice service. ``primary`` and ``shared`` are app-wide singletons.

    A fault-injection (demo) session never touches the real model or any shared state: it gets
    a simulated model plus its own private budget, cache and breaker.
    """
    if fault_rate > 0:
        primary = TemplateProvider(name="simulated-model")
        shared = build_shared_state(settings)
    shared = shared or build_shared_state(settings)
    return VoiceService(
        primary,
        timeout_s=settings.llm_timeout_s,
        retries=settings.llm_retries,
        min_significance=settings.llm_min_significance,
        breaker=shared.breaker,
        budget=shared.budget,
        cache=shared.cache,
        faults=FaultInjector(fault_rate, seed) if fault_rate > 0 else None,
        client_limiter=shared.per_client,
        client_key=client_key,
        on_event=on_event,
    )
