from __future__ import annotations

from collections import Counter

import pytest

from app.agents.personalizer import PersonalizerAgent
from app.agents.profile import ViewerProfile
from app.agents.stats import StatsAgent
from app.agents.storyteller import StorytellerAgent
from app.providers import VoiceService
from app.schema import Language, Mode
from app.simulator import MatchSimulator


def _by_key(full_run, profile):
    return full_run[1][profile.key]


def test_analyst_and_casual_are_genuinely_different(full_run, four_profiles):
    analyst, casual = (_by_key(full_run, p) for p in four_profiles[:2])
    ca, cc = Counter(o.type for o in analyst), Counter(o.type for o in casual)
    assert len(analyst) > 1.8 * len(casual)
    assert ca["stat_panel"] > cc["stat_panel"] and ca["momentum"] > cc["momentum"]
    a_panel = next(o for o in analyst if o.type == "stat_panel")
    c_panel = next(o for o in casual if o.type == "stat_panel")
    assert len(next(iter(a_panel.data["teams"].values()))) > len(next(iter(c_panel.data["teams"].values())))
    # Casual only surfaces bigger moments, analyst sees the detail.
    assert min(o.significance for o in casual if o.type in ("commentary", "insight")) >= 0.6
    assert min(o.significance for o in analyst if o.type in ("commentary", "insight")) < 0.6
    # Same goal, different voice.
    ag = next(o for o in analyst if o.type == "insight" and o.data["key"] == "goal")
    cg = next(o for o in casual if o.type == "insight" and o.data["key"] == "goal")
    assert ag.seq == cg.seq and ag.headline != cg.headline
    assert "xG" in ag.headline and "xG" not in cg.headline
    assert len(ag.evidence) > len(cg.evidence)


def test_favorite_club_gets_fan_alerts_and_perspective(full_run, four_profiles, meta):
    fan = _by_key(full_run, four_profiles[2])
    alerts = [o for o in fan if o.type == "fan_alert"]
    goals = [o for o in fan if o.type == "insight" and o.data["key"] == "goal"]
    assert len(alerts) == len(goals) > 0
    assert all(o.audience.favorite_club == meta.home.id for o in fan)
    moms = [o for o in fan if o.type == "momentum"]
    assert moms and all(o.data["perspective_team"] == meta.home.id for o in moms)
    assert any(o.data.get("favorite_club") for o in fan if o.type in ("commentary", "insight"))


def test_player_focus_mode(full_run, four_profiles, meta):
    prof = four_profiles[3]
    focus = _by_key(full_run, prof)
    cards = [o for o in focus if o.type == "player_card"]
    assert len(cards) >= 18
    assert all(c.data["player_id"] == prof.focus_player for c in cards)
    assert any(
        o.data.get("focus_player")
        for o in focus
        if o.type in ("commentary", "insight", "player_tag", "milestone")
    )


def test_language_is_applied(full_run, four_profiles):
    fr = _by_key(full_run, four_profiles[3])
    es = _by_key(full_run, four_profiles[2])
    assert any("Coup d'envoi" in (o.text or "") for o in fr)
    assert any("Arranca" in (o.text or "") for o in es)
    assert all(o.audience.language == "fr" for o in fr)


def test_every_profile_gets_scorebug_and_recap(full_run, four_profiles):
    for p in four_profiles:
        overlays = _by_key(full_run, p)
        types = Counter(o.type for o in overlays)
        assert types["recap"] == 1 and types["scorebug"] >= 4


def test_invalid_profile_rejected():
    sim = MatchSimulator(1)
    stats = StatsAgent(sim.meta)
    st = StorytellerAgent(sim.meta, stats, VoiceService(None))
    with pytest.raises(ValueError):
        PersonalizerAgent(sim.meta, stats, st, ViewerProfile(Mode.CASUAL, Language.EN, favorite_club="ZZZ"))
    with pytest.raises(ValueError):
        PersonalizerAgent(sim.meta, stats, st, ViewerProfile(Mode.CASUAL, Language.EN, focus_player="ZZZ-99"))
