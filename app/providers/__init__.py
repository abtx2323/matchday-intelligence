"""Pluggable language providers (template offline default, Microsoft Foundry / Azure OpenAI)."""

from .base import LanguageProvider, ProviderError, VoiceRequest, VoiceResult
from .factory import build_voice_service
from .resilient import VoiceService
from .template import TemplateProvider

__all__ = [
    "LanguageProvider",
    "ProviderError",
    "TemplateProvider",
    "VoiceRequest",
    "VoiceResult",
    "VoiceService",
    "build_voice_service",
]
