from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ..config import VALID_PROVIDERS, Settings
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
    )


def build_voice_service(
    settings: Settings,
    *,
    seed: int = 0,
    fault_rate: float = 0.0,
    primary: LanguageProvider | None = None,
    shared: SharedVoiceState | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> VoiceService:
    """Per-session voice service. ``primary`` and ``shared`` are app-wide singletons."""
    if primary is None and fault_rate > 0:
        # Offline demo of failure recovery: a simulated "model" that the injector can break.
        primary = TemplateProvider(name="simulated-model")
        shared = None  # never let simulated faults touch the app-wide breaker/cache
    shared = shared or build_shared_state(settings)
    # Injected faults must not trip the app-wide breaker for other viewers.
    breaker = (
        CircuitBreaker(settings.breaker_threshold, settings.breaker_cooldown_s)
        if fault_rate > 0
        else shared.breaker
    )
    return VoiceService(
        primary,
        timeout_s=settings.llm_timeout_s,
        retries=settings.llm_retries,
        min_significance=settings.llm_min_significance,
        breaker=breaker,
        budget=shared.budget,
        cache=shared.cache,
        faults=FaultInjector(fault_rate, seed) if fault_rate > 0 else None,
        on_event=on_event,
    )
