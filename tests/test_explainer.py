from __future__ import annotations

from app.agents.explainer import BIG_CHANCE_XG, CONSOLATION_MINUTE, ExplainerAgent
from app.agents.stats import StatsAgent
from app.models import EventType
from app.simulator import MatchSimulator


def _run(seed):
    sim = MatchSimulator(seed)
    stats = StatsAgent(sim.meta)
    ex = ExplainerAgent(sim.meta, stats)
    for e in sim.events():
        ex.analyze(stats.ingest(e))
    return sim, ex


def test_every_goal_is_explained_with_evidence():
    sim, ex = _run(2)
    goals = [e for e in sim.events() if e.type == EventType.SHOT and e.outcome == "goal"]
    goal_insights = [i for i in ex.insights if i.kind == "goal"]
    assert len(goal_insights) == len(goals) > 0
    for ins, ev in zip(goal_insights, goals, strict=True):
        assert ins.seq == ev.seq
        assert ins.evidence["xg"] == round(ev.xg, 2)
        assert ins.reasons[0][0] in ("go_ahead", "equaliser", "extends_lead", "pulls_back", "consolation")
        assert ins.significance >= 0.9


def test_scoreline_reasoning_is_correct():
    sim, ex = _run(2)
    for ins in (i for i in ex.insights if i.kind == "goal"):
        score = ins.evidence["score"]
        team = ins.team_id
        opp = sim.meta.opponent(team).id
        before = score[team] - 1 - score[opp]
        expected = {0: "go_ahead", -1: "equaliser"}.get(before)
        if expected is None and before >= 1:
            expected = "extends_lead"
        elif expected is None:
            late = ins.params["minute"] > CONSOLATION_MINUTE
            expected = "consolation" if late else "pulls_back"
        assert ins.reasons[0][0] == expected


def test_big_chances_respect_threshold():
    _, ex = _run(3)
    for ins in ex.insights:
        if ins.kind == "big_chance":
            assert ins.evidence["xg"] >= BIG_CHANCE_XG


def test_explainer_covers_control_chaos_pressure_and_momentum():
    kinds = set()
    for seed in range(1, 15):
        _, ex = _run(seed)
        kinds |= {i.kind for i in ex.insights}
    assert {
        "momentum_swing",
        "rhythm_control",
        "rhythm_chaos",
        "pressure_shift",
        "elite_pass",
        "goal",
        "big_chance",
    } <= kinds


def test_insights_always_carry_evidence_and_reasons():
    _, ex = _run(7)
    assert ex.insights
    for ins in ex.insights:
        assert ins.reasons and ins.evidence
        assert 0 <= ins.significance <= 1


def test_consolation_only_late_in_match():
    from app.agents.stats import StatsUpdate
    from app.models import MatchEvent

    sim = MatchSimulator(1)
    home, away = sim.meta.home.id, sim.meta.away.id
    scorer = sim.meta.home.players[9].id

    def goal_at(clock_min: int) -> str:
        stats = StatsAgent(sim.meta)
        ex = ExplainerAgent(sim.meta, stats)
        ev = MatchEvent(
            seq=1,
            t=clock_min * 60 + 120,
            period=2,
            type=EventType.SHOT,
            team_id=home,
            player_id=scorer,
            x=95,
            y=34,
            outcome="goal",
            xg=0.3,
            speed_kmh=90,
            clock_s=clock_min * 60,
        )
        upd = StatsUpdate(ev, [], None, 0.0, {home: 1, away: 3})
        return next(i for i in ex.analyze(upd) if i.kind == "goal").reasons[0][0]

    assert goal_at(60) == "pulls_back"
    assert goal_at(82) == "consolation"
