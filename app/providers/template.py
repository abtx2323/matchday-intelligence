from __future__ import annotations

from .base import VoiceRequest


class TemplateProvider:
    """Offline, deterministic provider: returns the pre-rendered multilingual template text.

    Used as the default (no cloud credentials needed), as the fallback for every cloud call,
    and in all tests.
    """

    def __init__(self, name: str = "template") -> None:
        self.name = name

    async def voice(self, request: VoiceRequest) -> str:
        return request.fallback_text
