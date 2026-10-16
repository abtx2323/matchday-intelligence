from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ..config import Settings
from .base import LanguageProvider
from .resilient import CircuitBreaker, FaultInjector, VoiceService
from .template import TemplateProvider

log = logging.getLogger(__name__)


def build_primary(settings: Settings) -> LanguageProvider | None:
    """Return the configured cloud provider, or None for pure offline template mode."""
    if settings.provider in ("foundry", "azure_openai"):
        from .agent_framework_provider import AgentFrameworkProvider

        return AgentFrameworkProvider(settings.provider, settings)
    if settings.provider != "template":
        log.warning("Unknown MDI_PROVIDER %r; using offline template provider", settings.provider)
    return None


def build_voice_service(
    settings: Settings,
    *,
    seed: int = 0,
    fault_rate: float = 0.0,
    primary: LanguageProvider | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> VoiceService:
    if primary is None:
        primary = build_primary(settings)
    if primary is None and fault_rate > 0:
        # Offline demo of failure recovery: a simulated "model" that the injector can break.
        primary = TemplateProvider(name="simulated-model")
    return VoiceService(
        primary,
        timeout_s=settings.llm_timeout_s,
        retries=settings.llm_retries,
        min_significance=settings.llm_min_significance,
        breaker=CircuitBreaker(settings.breaker_threshold, settings.breaker_cooldown_s),
        faults=FaultInjector(fault_rate, seed) if fault_rate > 0 else None,
        on_event=on_event,
    )
