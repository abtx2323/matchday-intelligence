from __future__ import annotations

import asyncio
import statistics

from app.clubs import CLUBS
from app.models import PITCH_LENGTH, PITCH_WIDTH, EventType
from app.simulator import MatchSimulator, shot_xg

REAL_CLUB_WORDS = (
    "arsenal",
    "chelsea",
    "liverpool",
    "manchester",
    "tottenham",
    "everton",
    "newcastle",
    "aston villa",
    "west ham",
    "brighton",
    "fulham",
    "brentford",
    "wolves",
    "crystal palace",
    "bournemouth",
    "nottingham",
    "premier league",
)


def test_same_seed_is_identical():
    a = [e.to_dict() for e in MatchSimulator(123).events()]
    b = [e.to_dict() for e in MatchSimulator(123).events()]
    assert a == b
    assert MatchSimulator(123).meta.to_dict() == MatchSimulator(123).meta.to_dict()


def test_different_seeds_differ():
    a = [e.to_dict() for e in MatchSimulator(1).events()]
    b = [e.to_dict() for e in MatchSimulator(2).events()]
    assert a != b


def test_event_stream_is_well_formed():
    sim = MatchSimulator(42)
    events = sim.events()
    assert events[0].type == EventType.KICKOFF
    assert events[-1].type == EventType.FULL_TIME
    assert sum(e.type == EventType.HALF_TIME for e in events) == 1
    second_half = next(e for e in events if e.period == 2)
    assert second_half.type == EventType.KICKOFF and second_half.clock == "45:00"
    assert events[-1].minute >= 90
    seqs = [e.seq for e in events]
    assert seqs == list(range(1, len(events) + 1))
    ids = {p.id for p in sim.meta.all_players()}
    prev_t = 0.0
    for e in events:
        assert e.t >= prev_t  # feed time is monotonic
        prev_t = e.t
        for x in (e.x, e.end_x):
            if x is not None:
                assert 0 <= x <= PITCH_LENGTH
        for y in (e.y, e.end_y):
            if y is not None:
                assert 0 <= y <= PITCH_WIDTH
        for pid in (e.player_id, e.receiver_id, e.opponent_id):
            assert pid is None or pid in ids
        if e.type == EventType.PASS:
            assert e.distance_m is not None and e.outcome in ("complete", "incomplete")
            assert e.speed_kmh is not None and 0 < e.speed_kmh <= 110
        if e.type == EventType.SHOT:
            assert 0 < (e.xg or 0) < 1 and e.speed_kmh and e.outcome


def test_final_score_matches_goals():
    events = MatchSimulator(9).events()
    goals = [e for e in events if e.type == EventType.SHOT and e.outcome == "goal"]
    final = events[-1].extra["score"]
    assert sum(final.values()) == len(goals)
    for team_id, n in final.items():
        assert n == sum(g.team_id == team_id for g in goals)


def test_football_realism_across_seeds():
    goals, acc, shots = [], [], []
    for seed in range(1, 26):
        ev = MatchSimulator(seed).events()
        passes = [e for e in ev if e.type == EventType.PASS]
        goals.append(sum(ev[-1].extra["score"].values()))
        acc.append(sum(p.outcome == "complete" for p in passes) / len(passes))
        shots.append(sum(e.type == EventType.SHOT for e in ev))
    assert 1.5 <= statistics.mean(goals) <= 4.5
    assert 0.75 <= statistics.mean(acc) <= 0.9
    assert 15 <= statistics.mean(shots) <= 35


def test_xg_model_is_sensible():
    assert shot_xg(99, 34) > shot_xg(88, 34) > shot_xg(80, 34)
    assert shot_xg(94, 34) > shot_xg(94, 5)
    assert shot_xg(94, 34, pressure=1.0) < shot_xg(94, 34)


def test_only_fictional_names():
    for club in CLUBS:
        names = [club["name"].lower()] + [p[0].lower() for p in club["players"]]
        for n in names:
            assert not any(w in n for w in REAL_CLUB_WORDS)


def test_stream_resume_yields_all_events_without_waiting():
    sim = MatchSimulator(5)

    async def collect():
        return [e async for e in sim.stream(speed=0)]

    assert len(asyncio.run(collect())) == len(sim.events())
