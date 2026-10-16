from __future__ import annotations

import asyncio

import pytest

from app.agents.profile import ViewerProfile
from app.orchestrator import MatchSession
from app.schema import Language, Mode
from app.simulator import MatchSimulator

SEED = 6


@pytest.fixture(scope="session")
def meta():
    return MatchSimulator(SEED).meta


def run_session(seed: int, profiles: list[ViewerProfile], **kw) -> tuple[MatchSession, dict]:
    session = MatchSession(seed, profiles, **kw)
    timeline = asyncio.run(session.run_all())
    return session, timeline


@pytest.fixture(scope="session")
def four_profiles(meta):
    return [
        ViewerProfile(Mode.ANALYST, Language.EN),
        ViewerProfile(Mode.CASUAL, Language.EN),
        ViewerProfile(Mode.CASUAL, Language.ES, favorite_club=meta.home.id),
        ViewerProfile(Mode.ANALYST, Language.FR, focus_player=meta.away.players[9].id),
    ]


@pytest.fixture(scope="session")
def full_run(four_profiles):
    return run_session(SEED, four_profiles)
