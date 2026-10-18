from __future__ import annotations

import asyncio
from collections import Counter

from app.agents.profile import ViewerProfile
from app.orchestrator import MatchSession
from app.schema import Language, Mode

from .conftest import run_session

P = [ViewerProfile(Mode.ANALYST, Language.EN)]


def _dump(timeline):
    # agent_trace overlays carry wall-clock latencies (operational telemetry), so they are
    # excluded; every match-intelligence overlay must be byte-for-byte reproducible.
    return {k: [o.model_dump(mode="json") for o in v if o.type != "agent_trace"] for k, v in timeline.items()}


def test_pipeline_is_deterministic():
    _, a = run_session(3, P)
    _, b = run_session(3, P)
    assert _dump(a) == _dump(b)


def test_all_agents_hand_off_and_are_traced(full_run):
    session, timeline = full_run
    summary = session.trace.summary()
    for agent in ("simulator", "stats", "explainer", "storyteller", "personalizer"):
        assert summary["agents"][agent]["calls"] > 0
        assert summary["agents"][agent]["degraded"] == 0
    assert summary["handoffs"] > 4 * len(session.state.events)
    insight = next(o for v in timeline.values() for o in v if o.type == "insight")
    assert insight.source_agents == ["stats", "explainer", "storyteller", "personalizer"]
    assert session.state.finished


def test_agent_failures_are_retried_and_degraded_without_stopping_match():
    session, timeline = run_session(3, P, agent_fault_rate=0.3)
    summary = session.trace.summary()["agents"]
    assert sum(summary[a]["retried"] for a in ("explainer", "storyteller", "personalizer")) > 0
    assert sum(summary[a]["degraded"] for a in ("explainer", "storyteller", "personalizer")) > 0
    overlays = timeline[P[0].key]
    traces = [o for o in overlays if o.type == "agent_trace"]
    statuses = Counter(o.data.get("status") for o in traces)
    assert statuses["retried"] > 0 and statuses["degraded"] > 0
    assert session.state.finished
    assert any(o.type == "recap" for o in overlays)


def test_provider_outage_falls_back_to_templates():
    session, timeline = run_session(3, P, fault_rate=0.5)
    voice = session.voice.stats
    assert voice["fallbacks"] > 0 and voice["primary_ok"] > 0
    overlays = timeline[P[0].key]
    voiced = [o for o in overlays if o.type in ("insight", "commentary", "recap")]
    assert all(o.text for o in voiced)
    assert any(o.provider == "template(fallback)" for o in voiced)
    assert any(o.provider == "simulated-model" for o in voiced)
    assert any(o.type == "agent_trace" and o.data.get("agent") == "storyteller.voice" for o in overlays)


def test_stats_failure_skips_event_but_match_continues():
    session = MatchSession(4, P)
    original = session.stats.ingest
    calls = {"n": 0}

    def flaky(ev):
        calls["n"] += 1
        if calls["n"] == 50:
            raise RuntimeError("stats outage")
        return original(ev)

    session.stats.ingest = flaky  # type: ignore[method-assign]
    timeline = asyncio.run(session.run_all())
    assert session.state.finished
    assert session.trace.summary()["agents"]["stats"]["retried"] == 1
    assert any(o.type == "recap" for o in timeline[P[0].key])


def test_resume_from_position_matches_full_run_state():
    full, _ = run_session(6, P)
    resumed = MatchSession(6, P)

    async def go():
        out = []
        async for _ev, per in resumed.run(speed=0, start_t=3000):
            out.extend(per[P[0].key])
        return out

    overlays = asyncio.run(go())
    assert all(o.clock.t >= 3000 for o in overlays)
    assert resumed.stats.snapshot() == full.stats.snapshot()
    full_recap = next(o for o in full.state.narrative if o.key == "full_time")
    assert any(o.type == "recap" for o in overlays) and full_recap
