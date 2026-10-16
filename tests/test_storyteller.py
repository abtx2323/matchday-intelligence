from __future__ import annotations

import asyncio
import re

from app.agents import i18n
from app.agents.explainer import ExplainerAgent
from app.agents.profile import ViewerProfile
from app.agents.stats import StatsAgent
from app.agents.storyteller import StorytellerAgent
from app.providers import VoiceService
from app.schema import Language, Mode
from app.simulator import MatchSimulator

PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


def test_every_template_has_all_languages():
    for key, modes in i18n.T.items():
        for mode, langs in modes.items():
            assert set(langs) == set(i18n.LANGS), (key, mode)
    for table in (i18n.WHY, i18n.LABELS, i18n.RECAP):
        for key, langs in table.items():
            assert set(langs) == set(i18n.LANGS), key


def _story(seed=6):
    sim = MatchSimulator(seed)
    stats = StatsAgent(sim.meta)
    ex = ExplainerAgent(sim.meta, stats)
    st = StorytellerAgent(sim.meta, stats, VoiceService(None))
    for e in sim.events():
        upd = stats.ingest(e)
        st.narrate(upd, ex.analyze(upd))
    return sim, ex, st


def test_narrative_covers_key_moments():
    _, _, st = _story()
    keys = {i.key for i in st.items}
    assert {"kickoff", "half_time", "full_time", "goal"} <= keys
    assert any(i.overlay_type == "insight" for i in st.items)
    assert any(i.overlay_type in ("milestone", "player_tag") for i in st.items)


def test_voiced_text_has_no_unfilled_placeholders_in_any_language():
    _, _, st = _story()

    async def voice_all():
        out = []
        for lang in Language:
            for mode in Mode:
                prof = ViewerProfile(mode, lang)
                for item in st.items[:80]:
                    v = await st.voice(item, prof)
                    out.append((v.headline or "") + " " + v.text)
        return out

    texts = asyncio.run(voice_all())
    assert texts
    for t in texts:
        assert not PLACEHOLDER.search(t), t
        assert " ? " not in f" {t} ", t


def test_languages_produce_different_text():
    _, _, st = _story()
    goal = next(i for i in st.items if i.key == "goal")

    async def voice(lang):
        return (await st.voice(goal, ViewerProfile(Mode.CASUAL, lang))).headline

    en, es, fr = (asyncio.run(voice(lang)) for lang in Language)
    assert len({en, es, fr}) == 3
    assert "GOAL" in en and "GOL" in es and "BUT" in fr


def test_recap_in_three_languages_and_personalized(meta=None):
    sim, ex, st = _story()
    facts = st.recap_facts(ex.insights)
    focus = sim.meta.home.players[9]

    async def recap(profile):
        return await st.voice_recap(facts, profile)

    base = asyncio.run(recap(ViewerProfile(Mode.CASUAL, Language.EN)))
    es = asyncio.run(recap(ViewerProfile(Mode.CASUAL, Language.ES)))
    fr = asyncio.run(recap(ViewerProfile(Mode.CASUAL, Language.FR)))
    analyst = asyncio.run(recap(ViewerProfile(Mode.ANALYST, Language.EN)))
    personal = asyncio.run(
        recap(ViewerProfile(Mode.CASUAL, Language.EN, favorite_club=sim.meta.home.id, focus_player=focus.id))
    )
    assert len({base["text"], es["text"], fr["text"]}) == 3
    assert "Key moments" in analyst["text"] and "Key moments" not in base["text"]
    assert focus.name in personal["text"] and "Your player" in personal["text"]
    assert facts["score_line"] and facts["player_of_the_match"]["name"]
