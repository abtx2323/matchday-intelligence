from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol


class ProviderError(RuntimeError):
    """Raised by a provider when it cannot produce usable text."""


@dataclass(frozen=True)
class VoiceRequest:
    """A request to turn structured facts into one natural-language line."""

    task: str  # "commentary" | "insight" | "recap"
    key: str  # template key, e.g. "goal"
    facts: dict[str, Any]
    language: str
    mode: str
    fallback_text: str  # deterministic template text in the requested language
    significance: float
    max_words: int = 40
    cache_key: str = ""


@dataclass
class VoiceResult:
    text: str
    provider: str
    fallback_used: bool = False
    attempts: int = 0
    latency_ms: float = 0.0
    errors: list[str] = field(default_factory=list)
    status: str = "ok"  # ok | retried | fallback | circuit_open | gated | cached


class LanguageProvider(Protocol):
    name: str

    async def voice(self, request: VoiceRequest) -> str: ...


_MD = re.compile(r"[*_`#>]+")
_WS = re.compile(r"\s+")


def sanitize_model_text(text: str, max_words: int) -> str:
    """Normalise model output for on-screen use; reject anything unusable."""
    if not text:
        raise ProviderError("empty response")
    cleaned = _MD.sub("", text)
    cleaned = _WS.sub(" ", cleaned).strip().strip('"').strip("“”").strip()
    if not cleaned:
        raise ProviderError("empty response after cleaning")
    lowered = cleaned.lower()
    if "://" in lowered or "www." in lowered:
        raise ProviderError("response contained a URL")
    words = cleaned.split(" ")
    hard_cap = int(max_words * 1.5) + 5
    if len(words) > hard_cap:
        cleaned = " ".join(words[:hard_cap]).rstrip(",;:") + "…"
    return cleaned[:1100]
