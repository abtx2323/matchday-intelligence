"""Orchestrator: runs the five agents over a shared match state with explicit handoffs.

Handoff order for every simulated event::

    Simulator --event--> Stats --StatsUpdate--> Explainer --Insights--> Storyteller
        --NarrativeItems--> Personalizer (one per viewer profile) --OverlayEvents--> renderer

Every hop goes through ``_call``, which times it, retries transient failures, and degrades
gracefully (an agent that keeps failing yields an empty contribution for that event instead of
stopping the match). Language generation has its own resilience layer (``VoiceService``:
timeout, retry, circuit breaker, template fallback). Everything is recorded in a trace that is
streamed to renderers as ``agent_trace`` overlays.
"""

from __future__ import annotations

import inspect
import random
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from .agents.explainer import ExplainerAgent, Insight
from .agents.personalizer import PersonalizerAgent
from .agents.profile import ViewerProfile
from .agents.stats import StatsAgent, StatsUpdate
from .agents.storyteller import NarrativeItem, StorytellerAgent
from .config import Settings
from .models import EventType, MatchEvent
from .providers import LanguageProvider, build_voice_service
from .schema import Display, MatchClock, OverlayEvent, OverlayType, Slot
from .simulator import MatchSimulator

AGENTS = ("simulator", "stats", "explainer", "storyteller", "personalizer")


class InjectedAgentFault(RuntimeError):
    pass


@dataclass
class TraceEntry:
    t: float
    seq: int
    agent: str
    status: str  # ok | retried | degraded | fallback | circuit_open
    attempts: int = 1
    latency_ms: float = 0.0
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "t": round(self.t, 1),
            "seq": self.seq,
            "agent": self.agent,
            "status": self.status,
            "attempts": self.attempts,
            "latency_ms": round(self.latency_ms, 2),
            "detail": self.detail,
        }


@dataclass
class AgentCounters:
    calls: int = 0
    ok: int = 0
    retried: int = 0
    degraded: int = 0
    total_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "ok": self.ok,
            "retried": self.retried,
            "degraded": self.degraded,
            "avg_ms": round(self.total_ms / self.calls, 3) if self.calls else 0.0,
        }


@dataclass
class TraceLog:
    entries: deque[TraceEntry] = field(default_factory=lambda: deque(maxlen=1000))
    counters: dict[str, AgentCounters] = field(
        default_factory=lambda: {a: AgentCounters() for a in (*AGENTS, "voice")}
    )
    pending: list[TraceEntry] = field(default_factory=list)
    handoffs: int = 0

    def record(self, entry: TraceEntry) -> None:
        self.entries.append(entry)
        if entry.status != "ok":
            self.pending.append(entry)

    def summary(self) -> dict[str, Any]:
        return {"agents": {k: v.to_dict() for k, v in self.counters.items()}, "handoffs": self.handoffs}


@dataclass
class SharedMatchState:
    """The blackboard every agent reads from and writes its own slice to."""

    events: list[MatchEvent] = field(default_factory=list)
    insights: list[Insight] = field(default_factory=list)
    narrative: list[NarrativeItem] = field(default_factory=list)
    last_update: StatsUpdate | None = None
    finished: bool = False


class MatchSession:
    def __init__(
        self,
        seed: int,
        profiles: list[ViewerProfile],
        settings: Settings | None = None,
        *,
        primary: LanguageProvider | None = None,
        fault_rate: float = 0.0,
        agent_fault_rate: float = 0.0,
        max_attempts: int = 2,
    ) -> None:
        self.settings = settings or Settings()
        self.seed = seed
        self.trace = TraceLog()
        self.state = SharedMatchState()
        self.simulator = MatchSimulator(seed)
        self.meta = self.simulator.meta
        self._current: tuple[float, int] = (0.0, 0)
        self.voice = build_voice_service(
            self.settings, seed=seed, fault_rate=fault_rate, primary=primary, on_event=self._on_voice_event
        )
        self.stats = StatsAgent(self.meta)
        self.explainer = ExplainerAgent(self.meta, self.stats)
        self.storyteller = StorytellerAgent(self.meta, self.stats, self.voice)
        self.profiles = profiles
        self.personalizers = {
            p.key: PersonalizerAgent(self.meta, self.stats, self.storyteller, p) for p in profiles
        }
        self.max_attempts = max(1, max_attempts)
        self._agent_fault_rate = max(0.0, min(0.5, agent_fault_rate))
        self._agent_rng = random.Random(seed * 104729 + 3)
        self._last_summary_minute = -1

    # ------------------------------------------------------------------ plumbing
    def _on_voice_event(self, info: dict[str, Any]) -> None:
        t, seq = self._current
        status = str(info.get("status", "fallback"))
        c = self.trace.counters["voice"]
        if status == "retried":
            c.retried += 1
        else:
            c.degraded += 1
        detail = f"{info.get('provider')} {info.get('key')}"
        if info.get("errors"):
            detail += f": {info['errors'][-1]}"
        if info.get("circuit_opened"):
            detail += " (circuit opened)"
        self.trace.record(
            TraceEntry(t, seq, "storyteller.voice", status, int(info.get("attempts", 1)), 0.0, detail[:240])
        )

    async def _call(
        self, agent: str, fn: Callable[..., Any], *args: Any, fallback: Any = None, inject: bool = True
    ) -> Any:
        t, seq = self._current
        c = self.trace.counters[agent]
        start = time.perf_counter()
        last_err = ""
        for attempt in range(1, self.max_attempts + 1):
            c.calls += 1
            try:
                if inject and self._agent_fault_rate and self._agent_rng.random() < self._agent_fault_rate:
                    raise InjectedAgentFault(f"injected fault in {agent}")
                result = fn(*args)
                if inspect.isawaitable(result):
                    result = await result
                elapsed = (time.perf_counter() - start) * 1000
                c.total_ms += elapsed
                if attempt > 1:
                    c.retried += 1
                    self.trace.record(TraceEntry(t, seq, agent, "retried", attempt, elapsed, last_err))
                else:
                    c.ok += 1
                self.trace.handoffs += 1
                return result
            except Exception as exc:  # noqa: BLE001 - agents must never crash the match
                last_err = f"{type(exc).__name__}: {str(exc)[:160]}"
        elapsed = (time.perf_counter() - start) * 1000
        c.total_ms += elapsed
        c.degraded += 1
        self.trace.record(TraceEntry(t, seq, agent, "degraded", self.max_attempts, elapsed, last_err))
        return fallback

    def _trace_overlays(self, ev: MatchEvent, profile: ViewerProfile) -> list[OverlayEvent]:
        out: list[OverlayEvent] = []
        clock = MatchClock(t=round(ev.t, 1), period=ev.period, display=ev.clock)
        for i, entry in enumerate(self.trace.pending):
            out.append(
                OverlayEvent(
                    id=f"ov-{ev.seq}-agent_trace-{i}",
                    match_id=self.meta.match_id,
                    seq=ev.seq,
                    clock=clock,
                    type=OverlayType.AGENT_TRACE,
                    display=Display(slot=Slot.HIDDEN, duration_ms=0, priority=0),
                    headline=f"{entry.agent}: {entry.status}",
                    text=entry.detail or None,
                    data=entry.to_dict(),
                    significance=0.0,
                    source_agents=["orchestrator"],
                    provider="none",
                    audience=profile.audience(),
                )
            )
        minute = ev.mark_minute
        if ev.type == EventType.TRACKING and minute % 5 == 0 and minute != self._last_summary_minute:
            out.append(
                OverlayEvent(
                    id=f"ov-{ev.seq}-agent_trace-summary",
                    match_id=self.meta.match_id,
                    seq=ev.seq,
                    clock=clock,
                    type=OverlayType.AGENT_TRACE,
                    display=Display(slot=Slot.HIDDEN, duration_ms=0, priority=0),
                    headline="pipeline summary",
                    data={
                        **self.trace.summary(),
                        "voice": dict(self.voice.stats),
                        "provider": self.voice.primary_name,
                        "circuit_open": self.voice.breaker.opened_at is not None,
                    },
                    significance=0.0,
                    source_agents=["orchestrator"],
                    provider="none",
                    audience=profile.audience(),
                )
            )
        return out

    # ------------------------------------------------------------------ pipeline
    async def step(self, ev: MatchEvent, render: bool = True) -> dict[str, list[OverlayEvent]]:
        self._current = (ev.t, ev.seq)
        self.state.events.append(ev)
        self.trace.counters["simulator"].calls += 1
        self.trace.counters["simulator"].ok += 1

        upd: StatsUpdate | None = await self._call("stats", self.stats.ingest, ev, inject=False)
        results: dict[str, list[OverlayEvent]] = {p.key: [] for p in self.profiles}
        if upd is None:
            return results  # Stats degraded: skip downstream for this event, keep the match alive
        self.state.last_update = upd

        insights: list[Insight] = await self._call("explainer", self.explainer.analyze, upd, fallback=[])
        self.state.insights.extend(insights)
        items: list[NarrativeItem] = await self._call(
            "storyteller", self.storyteller.narrate, upd, insights, fallback=[]
        )
        self.state.narrative.extend(items)

        if not render:
            self.trace.pending.clear()
            return results

        for profile in self.profiles:
            agent = self.personalizers[profile.key]
            overlays = await self._call("personalizer", agent.render, upd, items, fallback=[])
            if ev.type == EventType.FULL_TIME:
                facts = self.storyteller.recap_facts(self.state.insights)
                recap = await self._call(
                    "personalizer", agent.render_recap, ev, facts, fallback=None, inject=False
                )
                if recap is not None:
                    overlays.append(recap)
            results[profile.key] = overlays
        for profile in self.profiles:
            results[profile.key].extend(self._trace_overlays(ev, profile))
        if ev.type == EventType.TRACKING and ev.mark_minute % 5 == 0:
            self._last_summary_minute = ev.mark_minute
        self.trace.pending.clear()
        if ev.type == EventType.FULL_TIME:
            self.state.finished = True
        return results

    async def run(
        self, speed: float = 0.0, start_t: float = 0.0
    ) -> AsyncIterator[tuple[MatchEvent, dict[str, list[OverlayEvent]]]]:
        async for ev in self.simulator.stream(speed=speed, start_t=start_t):
            render = ev.t >= start_t or ev.type == EventType.FULL_TIME
            yield ev, await self.step(ev, render=render)

    async def run_all(self) -> dict[str, list[OverlayEvent]]:
        timeline: dict[str, list[OverlayEvent]] = {p.key: [] for p in self.profiles}
        async for _ev, per_profile in self.run(speed=0):
            for k, v in per_profile.items():
                timeline[k].extend(v)
        return timeline
