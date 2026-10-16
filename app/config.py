"""Runtime configuration from environment variables. No secrets live in code.

Every numeric setting is parsed defensively: non-finite or unparsable values fall back to the
default, and every value is clamped to a sane range.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

VALID_PROVIDERS = ("template", "foundry", "azure_openai")


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _float(name: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        value = default
    if not math.isfinite(value):
        value = default
    return _clamp(value, lo, hi)


def _int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except ValueError:
        value = default
    return int(_clamp(value, lo, hi))


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
    llm_max_calls_total: int = 20_000
    llm_max_calls_per_hour: int = 600
    breaker_threshold: int = 3
    breaker_cooldown_s: float = 30.0
    allowed_origins: tuple[str, ...] = field(default_factory=tuple)
    max_concurrent_streams: int = 50
    max_streams_per_ip: int = 3
    max_stream_seconds: float = 900.0
    max_heavy_requests: int = 4
    rate_limit_per_minute: int = 120
    trusted_proxy_hops: int = 0
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
            llm_timeout_s=_float("MDI_LLM_TIMEOUT_S", 6.0, 0.5, 60.0),
            llm_retries=_int("MDI_LLM_RETRIES", 1, 0, 3),
            llm_min_significance=_float("MDI_LLM_MIN_SIGNIFICANCE", 0.7, 0.0, 1.0),
            llm_max_calls_total=_int("MDI_LLM_MAX_CALLS", 20_000, 0, 10_000_000),
            llm_max_calls_per_hour=_int("MDI_LLM_MAX_CALLS_PER_HOUR", 600, 0, 1_000_000),
            breaker_threshold=_int("MDI_BREAKER_THRESHOLD", 3, 1, 100),
            breaker_cooldown_s=_float("MDI_BREAKER_COOLDOWN_S", 30.0, 1.0, 3600.0),
            allowed_origins=origins,
            max_concurrent_streams=_int("MDI_MAX_STREAMS", 50, 1, 1000),
            max_streams_per_ip=_int("MDI_MAX_STREAMS_PER_IP", 3, 1, 100),
            max_stream_seconds=_float("MDI_MAX_STREAM_S", 900.0, 10.0, 7200.0),
            max_heavy_requests=_int("MDI_MAX_HEAVY_REQUESTS", 4, 1, 64),
            rate_limit_per_minute=_int("MDI_RATE_LIMIT_PER_MIN", 120, 1, 100_000),
            trusted_proxy_hops=_int("MDI_TRUSTED_PROXY_HOPS", 0, 0, 5),
            default_speed=_float("MDI_DEFAULT_SPEED", 30.0, 5.0, 600.0),
        )
