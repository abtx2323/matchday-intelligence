from __future__ import annotations

import asyncio

import pytest

from app.config import Settings
from app.providers import ProviderError, VoiceRequest, VoiceService
from app.providers.base import sanitize_model_text
from app.providers.factory import build_primary, build_voice_service
from app.providers.resilient import CircuitBreaker


def req(sig=0.9, key="goal", cache_key=""):
    return VoiceRequest(
        task="commentary",
        key=key,
        facts={"player": "A"},
        language="en",
        mode="casual",
        fallback_text="fallback line",
        significance=sig,
        cache_key=cache_key,
    )


class Flaky:
    name = "flaky"

    def __init__(self, fail_times=0, delay=0.0, text="model line"):
        self.fail_times, self.delay, self.text, self.calls = fail_times, delay, text, 0

    async def voice(self, request):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.calls <= self.fail_times:
            raise ProviderError("boom")
        return self.text


def test_template_only_service_returns_fallback():
    res = asyncio.run(VoiceService(None).voice(req()))
    assert res.text == "fallback line" and res.provider == "template"


def test_low_significance_is_gated_to_template():
    p = Flaky()
    res = asyncio.run(VoiceService(p, min_significance=0.7).voice(req(sig=0.3)))
    assert res.status == "gated" and p.calls == 0


def test_retry_then_success():
    events = []
    p = Flaky(fail_times=1)
    svc = VoiceService(p, retries=1, on_event=events.append)
    res = asyncio.run(svc.voice(req()))
    assert res.text == "model line" and res.attempts == 2 and res.status == "retried"
    assert events and events[0]["status"] == "retried"


def test_timeout_falls_back():
    svc = VoiceService(Flaky(delay=0.2), timeout_s=0.01, retries=0)
    res = asyncio.run(svc.voice(req()))
    assert res.fallback_used and res.text == "fallback line" and "timeout" in res.errors[0]


def test_circuit_breaker_opens_and_recovers():
    now = [0.0]
    breaker = CircuitBreaker(threshold=2, cooldown_s=10, clock=lambda: now[0])
    p = Flaky(fail_times=100)
    svc = VoiceService(p, retries=0, breaker=breaker)
    for _ in range(2):
        asyncio.run(svc.voice(req()))
    assert breaker.is_open
    calls = p.calls
    res = asyncio.run(svc.voice(req()))
    assert res.status == "circuit_open" and p.calls == calls  # provider not hammered
    now[0] = 11.0
    p.fail_times = 0
    res = asyncio.run(svc.voice(req()))
    assert res.text == "model line" and not breaker.is_open


def test_cache_avoids_repeat_calls():
    p = Flaky()
    svc = VoiceService(p)
    asyncio.run(svc.voice(req(cache_key="k")))
    res = asyncio.run(svc.voice(req(cache_key="k")))
    assert p.calls == 1 and res.status == "cached"


def test_sanitize_model_text():
    assert sanitize_model_text('  "**Big** goal!"  ', 10) == "Big goal!"
    with pytest.raises(ProviderError):
        sanitize_model_text("", 10)
    with pytest.raises(ProviderError):
        sanitize_model_text("see https://example.com", 10)
    long = sanitize_model_text("word " * 200, 10)
    assert len(long.split()) <= 21


def test_factory_defaults_offline_and_validates_cloud_config():
    assert build_primary(Settings()) is None
    svc = build_voice_service(Settings(), fault_rate=0.3, seed=1)
    assert svc.primary is not None and svc.primary.name == "simulated-model"
    # Incomplete / invalid cloud config or unknown provider fails fast at build time.
    for bad in (
        Settings(provider="foundry"),
        Settings(provider="azure_openai"),
        Settings(provider="foundry", foundry_project_endpoint="http://x", foundry_model="m"),
        Settings(provider="nonsense"),
    ):
        with pytest.raises(ProviderError):
            build_primary(bad)
