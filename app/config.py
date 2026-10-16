"""Runtime configuration from environment variables. No secrets live in code."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # "template" (offline, deterministic), "foundry" (Microsoft Foundry project via Agent
    # Framework), or "azure_openai" (Azure OpenAI deployment via Agent Framework).
    provider: str = "template"
    foundry_project_endpoint: str | None = None
    foundry_model: str | None = None
    azure_openai_endpoint: str | None = None
    azure_openai_deployment: str | None = None
    azure_openai_api_version: str = "2024-10-21"
    llm_timeout_s: float = 6.0
    llm_retries: int = 1
    llm_min_significance: float = 0.7
    breaker_threshold: int = 3
    breaker_cooldown_s: float = 30.0
    allowed_origins: tuple[str, ...] = field(default_factory=tuple)
    max_concurrent_streams: int = 50
    default_speed: float = 30.0

    @classmethod
    def from_env(cls) -> Settings:
        origins = tuple(o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip())
        return cls(
            provider=os.environ.get("MDI_PROVIDER", "template").strip().lower(),
            foundry_project_endpoint=os.environ.get("FOUNDRY_PROJECT_ENDPOINT") or None,
            foundry_model=os.environ.get("FOUNDRY_MODEL") or None,
            azure_openai_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT") or None,
            azure_openai_deployment=os.environ.get("AZURE_OPENAI_DEPLOYMENT") or None,
            azure_openai_api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
            llm_timeout_s=_float("MDI_LLM_TIMEOUT_S", 6.0),
            llm_retries=max(0, min(3, _int("MDI_LLM_RETRIES", 1))),
            llm_min_significance=_float("MDI_LLM_MIN_SIGNIFICANCE", 0.7),
            breaker_threshold=max(1, _int("MDI_BREAKER_THRESHOLD", 3)),
            breaker_cooldown_s=_float("MDI_BREAKER_COOLDOWN_S", 30.0),
            allowed_origins=origins,
            max_concurrent_streams=max(1, _int("MDI_MAX_STREAMS", 50)),
            default_speed=_float("MDI_DEFAULT_SPEED", 30.0),
        )
