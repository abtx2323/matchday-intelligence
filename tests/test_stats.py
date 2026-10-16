from __future__ import annotations

from app.agents.stats import SHOT_SPEED_TAG_KMH, SPRINT_TAG_KMH, StatsAgent, pass_difficulty
from app.models import EventType, MatchEvent
from app.simulator import MatchSimulator


def _run(seed=7):
    sim = MatchSimulator(seed)
    agent = StatsAgent(sim.meta)
    updates = [agent.ingest(e) for e in sim.events()]
    return sim, agent, updates


def test_pass_difficulty_is_monotonic_and_bounded():
    base = pass_difficulty(10, 0, 0, False)
    assert pass_difficulty(40, 0, 0, False) > base
    assert pass_difficulty(10, 0.9, 0, False) > base
    assert pass_difficulty(10, 0, 20, False) > base
    assert pass_difficulty(10, 0, 0, True) > base
    assert 0 <= pass_difficulty(500, 1, 500, True) <= 10


def test_counts_match_raw_events():
    sim, agent, _ = _run()
    snap = agent.snapshot()
    for tid, ts in snap["teams"].items():
        passes = [e for e in sim.events() if e.type == EventType.PASS and e.team_id == tid]
        assert ts["passes"] == len(passes)
        assert ts["passes_completed"] == sum(p.outcome == "complete" for p in passes)
        shots = [e for e in sim.events() if e.type == EventType.SHOT and e.team_id == tid]
        assert ts["shots"] == len(shots)
        assert abs(ts["xg"] - round(sum(s.xg for s in shots), 2)) < 0.011
    assert snap["score"] == sim.events()[-1].extra["score"]


def test_possession_sums_to_100():
    _, agent, _ = _run()
    teams = agent.snapshot()["teams"]
    assert abs(sum(t["possession_pct"] for t in teams.values()) - 100) < 0.2


def test_momentum_bounded():
    _, _, updates = _run()
    assert all(-100 <= u.momentum <= 100 for u in updates)
    assert any(abs(u.momentum) > 20 for u in updates)


def test_threshold_tags_fire_only_above_threshold():
    _, _, updates = _run(11)
    for u in updates:
        for s in u.signals:
            if s.kind == "tag_sprint":
                assert s.params["speed"] >= SPRINT_TAG_KMH
            if s.kind == "tag_shot_speed":
                assert s.params["speed"] >= SHOT_SPEED_TAG_KMH


def test_sprint_tag_triggered_by_synthetic_event():
    sim = MatchSimulator(1)
    agent = StatsAgent(sim.meta)
    pid = sim.meta.home.players[9].id
    slow = MatchEvent(
        seq=1,
        t=10,
        period=1,
        type=EventType.SPRINT,
        team_id=sim.meta.home.id,
        player_id=pid,
        x=50,
        y=30,
        speed_kmh=SPRINT_TAG_KMH - 1,
    )
    fast = MatchEvent(
        seq=2,
        t=20,
        period=1,
        type=EventType.SPRINT,
        team_id=sim.meta.home.id,
        player_id=pid,
        x=50,
        y=30,
        speed_kmh=SPRINT_TAG_KMH + 1.5,
    )
    assert agent.ingest(slow).signals == []
    sig = agent.ingest(fast).signals
    assert len(sig) == 1 and sig[0].kind == "tag_sprint" and sig[0].player_id == pid
    assert agent.player_snapshot(pid)["top_speed_kmh"] == round(SPRINT_TAG_KMH + 1.5, 1)


def test_distance_and_ratings_reasonable():
    _, agent, _ = _run()
    outfield_km = [p.distance_m / 1000 for p in agent.players.values() if p.distance_m > 6000]
    assert outfield_km and all(8 <= km <= 15 for km in outfield_km)
    assert all(3 <= p.rating() <= 10 for p in agent.players.values())


def test_window_aggregation():
    _, agent, _ = _run()
    w = agent.window(30, 5)
    assert w["minutes"] == 5
    for d in w["teams"].values():
        assert d["completed"] <= d["passes"]
