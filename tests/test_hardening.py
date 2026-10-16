"""Spend controls, abuse limits, config validation and error hygiene."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
from app.agents.profile import ViewerProfile
from app.config import Settings
from app.limits import RateLimiter, StreamSlots, client_ip
from app.main import create_app
from app.orchestrator import MatchSession
from app.providers import ProviderError, VoiceRequest, VoiceService
from app.providers.agent_framework_provider import AgentFrameworkProvider
from app.providers.factory import build_primary, build_shared_state, build_voice_service
from app.providers.resilient import ModelBudget, error_label
from app.schema import Language, Mode

P = [ViewerProfile(Mode.ANALYST, Language.EN)]
LOW = Settings(llm_min_significance=0.0)  # voice every item, to exercise the provider path


class Counting:
    name = "counting"

    def __init__(self, fail_msg: str | None = None):
        self.calls, self.fail_msg = 0, fail_msg

    async def voice(self, request):
        self.calls += 1
        if self.fail_msg:
            raise ProviderError(self.fail_msg)
        return "model line"


def req(sig=0.9):
    return VoiceRequest(
        task="commentary",
        key="goal",
        facts={},
        language="en",
        mode="casual",
        fallback_text="fb",
        significance=sig,
    )


# ----------------------------------------------------------------------------- spend controls
def test_model_budget_total_and_hourly():
    now = [0.0]
    b = ModelBudget(max_total=5, max_per_hour=2, clock=lambda: now[0])
    assert b.try_acquire() and b.try_acquire() and not b.try_acquire()
    now[0] = 3601
    assert b.try_acquire() and b.try_acquire()
    now[0] = 9000
    assert b.try_acquire() and not b.try_acquire()  # lifetime cap of 5 reached


def test_budget_exhaustion_falls_back_to_templates():
    p = Counting()
    svc = VoiceService(p, budget=ModelBudget(1, 100))
    assert asyncio.run(svc.voice(req())).text == "model line"
    res = asyncio.run(svc.voice(req()))
    assert res.status == "budget_exhausted" and res.text == "fb" and p.calls == 1


def test_cache_and_budget_are_shared_across_sessions():
    shared = build_shared_state(LOW)
    p = Counting()
    asyncio.run(MatchSession(6, P, LOW, primary=p, shared=shared).run_all())
    first = p.calls
    assert first > 0
    asyncio.run(MatchSession(6, P, LOW, primary=p, shared=shared).run_all())
    assert p.calls == first  # same seed + items + language + mode: served from the app-wide cache
    asyncio.run(MatchSession(3, P, LOW, primary=p, shared=shared).run_all())
    assert p.calls > first  # a different match never reuses another match's text
    assert shared.budget.used_total == p.calls


def test_circuit_breaker_is_app_wide():
    shared = build_shared_state(LOW)
    p = Counting(fail_msg="down")
    asyncio.run(MatchSession(6, P, LOW, primary=p, shared=shared).run_all())
    assert shared.breaker.is_open
    calls = p.calls
    s2 = MatchSession(3, P, LOW, primary=p, shared=shared)
    asyncio.run(s2.run_all())
    assert p.calls == calls and s2.voice.stats["fallbacks"] > 0


def test_injected_faults_never_touch_shared_breaker():
    shared = build_shared_state(LOW)
    svc = build_voice_service(LOW, fault_rate=0.4, primary=Counting(), shared=shared)
    assert svc.breaker is not shared.breaker and svc.budget is shared.budget


# ----------------------------------------------------------------------------- error hygiene
def test_error_labels_are_generic():
    assert error_label(TimeoutError()) == "timeout"
    assert error_label(RuntimeError("https://internal.example/secret")) == "provider_error"


def test_no_overlay_leaks_exception_text():
    leak = "boom at https://internal.example/v1?token=abc"
    p = Counting(fail_msg=leak)
    s = MatchSession(6, P, LOW, primary=p, shared=build_shared_state(LOW), agent_fault_rate=0.2)
    original = s.explainer.analyze
    n = {"i": 0}

    def flaky(upd):
        n["i"] += 1
        if n["i"] % 97 == 0:
            raise RuntimeError(leak)
        return original(upd)

    s.explainer.analyze = flaky  # type: ignore[method-assign]
    timeline = asyncio.run(s.run_all())
    blob = json.dumps([o.model_dump(mode="json") for v in timeline.values() for o in v])
    assert "://" not in blob and "token=" not in blob
    assert "agent_error" in blob


# ----------------------------------------------------------------------------- limits
def test_rate_limiter_and_stream_slots():
    now = [0.0]
    rl = RateLimiter(2, 60, clock=lambda: now[0])
    assert rl.allow("a") and rl.allow("a") and not rl.allow("a") and rl.allow("b")
    now[0] = 61
    assert rl.allow("a")
    slots = StreamSlots(max_total=3, max_per_ip=2)
    assert slots.try_acquire("x") and slots.try_acquire("x") and not slots.try_acquire("x")
    assert slots.try_acquire("y") and not slots.try_acquire("z")  # global cap
    slots.release("x")
    assert slots.try_acquire("z")


def test_client_ip_respects_trusted_hops():
    class R:
        client = type("C", (), {"host": "10.0.0.1"})()
        headers = {"x-forwarded-for": "6.6.6.6, 1.2.3.4"}

    assert client_ip(R(), 0) == "10.0.0.1"
    assert client_ip(R(), 1) == "1.2.3.4"  # spoofed left-most entry ignored


def test_api_rate_limit_returns_429():
    with TestClient(create_app(Settings(rate_limit_per_minute=3))) as c:
        codes = [c.get("/api/match/1").status_code for _ in range(5)]
        assert codes[:3] == [200, 200, 200] and codes[3] == 429
        assert c.get("/healthz").status_code == 200  # health probes are not rate limited


@pytest.mark.parametrize(
    "q",
    ["speed=1", "speed=4.9", "speed=nan", "speed=inf", "start=inf", "start=nan", "faults=nan", "speed=-inf"],
)
def test_stream_rejects_bad_numbers(q):
    with TestClient(create_app(Settings())) as c:
        assert c.get(f"/api/stream?{q}").status_code == 422


def test_stream_max_duration_ends_stream():
    app = create_app(Settings(max_stream_seconds=0.0))
    with TestClient(app) as c, c.stream("GET", "/api/stream?seed=5&speed=0") as r:
        lines = [ln for ln in r.iter_lines() if ln.startswith("data: ")]
    end = json.loads(lines[-1][6:])
    assert end["kind"] == "end" and end["reason"] == "max_duration"


def test_heavy_endpoints_are_cached_and_sync():
    main_mod._timeline.cache_clear()
    with TestClient(create_app(Settings())) as c:
        a = c.get("/api/match/8/timeline").json()
        b = c.get("/api/match/8/timeline").json()
    assert a == b
    info = main_mod._timeline.cache_info()
    assert info.hits >= 1 and info.misses == 1
    assert not asyncio.iscoroutinefunction(main_mod._timeline.__wrapped__)


def test_recap_missing_is_clean_404(monkeypatch):
    monkeypatch.setattr(main_mod, "_recap", lambda seed, prof: None)
    with TestClient(create_app(Settings())) as c:
        r = c.get("/api/match/8/recap")
    assert r.status_code == 404 and r.json()["detail"] == "recap not available for this match"


def test_value_error_message_not_echoed():
    app = create_app(Settings())

    @app.get("/api/boom")
    async def boom():
        raise ValueError("internal detail xyz")

    with TestClient(app) as c:
        r = c.get("/api/boom")
    assert r.status_code == 422 and "xyz" not in r.text


# ----------------------------------------------------------------------------- configuration
def test_settings_reject_non_finite_and_clamp(monkeypatch):
    monkeypatch.setenv("MDI_LLM_TIMEOUT_S", "nan")
    monkeypatch.setenv("MDI_BREAKER_COOLDOWN_S", "inf")
    monkeypatch.setenv("MDI_DEFAULT_SPEED", "1")
    monkeypatch.setenv("MDI_LLM_RETRIES", "99")
    monkeypatch.setenv("MDI_LLM_MIN_SIGNIFICANCE", "-3")
    monkeypatch.setenv("MDI_MAX_STREAMS", "abc")
    s = Settings.from_env()
    assert s.llm_timeout_s == 6.0 and s.breaker_cooldown_s == 30.0
    assert s.default_speed == 5.0 and s.llm_retries == 3
    assert s.llm_min_significance == 0.0 and s.max_concurrent_streams == 50


def test_misconfigured_provider_fails_startup_without_creating_credential(monkeypatch):
    import azure.identity.aio as aio

    created = []
    monkeypatch.setattr(aio, "DefaultAzureCredential", lambda *a, **k: created.append(1))
    for bad in (Settings(provider="foundry"), Settings(provider="bogus")):
        with pytest.raises(ProviderError), TestClient(create_app(bad)):
            pass
    assert created == []


def test_healthz_does_not_expose_model_name():
    good = Settings(
        provider="foundry", foundry_project_endpoint="https://x.example", foundry_model="secret-model"
    )
    with TestClient(create_app(good)) as c:
        r = c.get("/healthz")
    assert r.status_code == 200 and r.json()["provider"] == "foundry"
    assert "secret-model" not in r.text
    assert build_primary(good).name == "foundry"


def test_provider_closes_client_and_credential_on_shutdown():
    closed = []

    class Closable:
        def __init__(self, tag):
            self.tag = tag

        async def close(self):
            closed.append(self.tag)

    p = AgentFrameworkProvider("foundry", Settings(foundry_model="m"), client=Closable("client"))
    p._credential = Closable("credential")
    asyncio.run(p.aclose())
    assert closed == ["client", "credential"]
