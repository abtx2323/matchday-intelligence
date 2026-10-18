"""Domain models shared by every agent (the "shared match state" vocabulary)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

PITCH_LENGTH = 105.0
PITCH_WIDTH = 68.0
HALF_SECONDS = 45 * 60


class EventType(StrEnum):
    KICKOFF = "kickoff"
    PASS = "pass"  # noqa: S105 - football pass, not a password
    CARRY = "carry"
    SHOT = "shot"
    TACKLE = "tackle"
    INTERCEPTION = "interception"
    PRESSURE = "pressure"
    POSSESSION_CHANGE = "possession_change"
    SPRINT = "sprint"
    TRACKING = "tracking"
    HALF_TIME = "half_time"
    FULL_TIME = "full_time"


@dataclass(frozen=True)
class Player:
    id: str
    name: str
    number: int
    position: str
    team_id: str
    base_x: float
    base_y: float
    skill: float  # 0..1 technical quality
    pace: float  # top speed potential in km/h
    work_rate: float  # km covered per 90 minutes


@dataclass(frozen=True)
class Team:
    id: str
    name: str
    short: str
    color: str
    style: str  # "possession" | "direct" | "pressing"
    strength: float
    players: tuple[Player, ...]

    def player(self, player_id: str) -> Player:
        for p in self.players:
            if p.id == player_id:
                return p
        raise KeyError(player_id)


@dataclass(frozen=True)
class MatchMeta:
    match_id: str
    seed: int
    competition: str
    venue: str
    kickoff_local: str
    home: Team
    away: Team

    def team(self, team_id: str) -> Team:
        if team_id == self.home.id:
            return self.home
        if team_id == self.away.id:
            return self.away
        raise KeyError(team_id)

    def opponent(self, team_id: str) -> Team:
        return self.away if team_id == self.home.id else self.home

    def all_players(self) -> list[Player]:
        return [*self.home.players, *self.away.players]

    def find_player(self, player_id: str) -> Player | None:
        for p in self.all_players():
            if p.id == player_id:
                return p
        return None

    def to_dict(self) -> dict[str, Any]:
        def team_dict(t: Team) -> dict[str, Any]:
            return {
                "id": t.id,
                "name": t.name,
                "short": t.short,
                "color": t.color,
                "style": t.style,
                "players": [
                    {"id": p.id, "name": p.name, "number": p.number, "position": p.position}
                    for p in t.players
                ],
            }

        return {
            "match_id": self.match_id,
            "seed": self.seed,
            "competition": self.competition,
            "venue": self.venue,
            "kickoff_local": self.kickoff_local,
            "home": team_dict(self.home),
            "away": team_dict(self.away),
            "synthetic": True,
        }


@dataclass
class MatchEvent:
    """One synthetic match event. Coordinates are metres; the home side attacks towards x=105."""

    seq: int
    t: float  # feed seconds since kickoff: monotonic, includes stoppage time, no half-time break
    period: int
    type: EventType
    team_id: str | None = None
    player_id: str | None = None
    x: float | None = None
    y: float | None = None
    end_x: float | None = None
    end_y: float | None = None
    distance_m: float | None = None
    outcome: str | None = None
    pressure: float | None = None  # 0..1 defensive pressure on the actor
    speed_kmh: float | None = None
    xg: float | None = None
    receiver_id: str | None = None
    opponent_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    clock_s: float | None = None  # displayed match clock (second half restarts at 45:00)

    @property
    def match_clock_s(self) -> float:
        return self.t if self.clock_s is None else self.clock_s

    @property
    def minute(self) -> int:
        """Displayed match minute (0-based), e.g. 0 for 00:00-00:59."""
        return int(self.match_clock_s // 60)

    @property
    def mark_minute(self) -> int:
        """Feed minute; for tracking snapshots, the exact minute mark they represent."""
        return int(self.extra.get("minute_mark", self.t // 60))

    @property
    def clock(self) -> str:
        m, s = divmod(int(self.match_clock_s), 60)
        return f"{m:02d}:{s:02d}"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "seq": self.seq,
            "t": round(self.t, 1),
            "clock": self.clock,
            "minute": self.minute + 1,
            "period": self.period,
            "type": str(self.type),
        }
        for key in (
            "team_id",
            "player_id",
            "x",
            "y",
            "end_x",
            "end_y",
            "distance_m",
            "outcome",
            "pressure",
            "speed_kmh",
            "xg",
            "receiver_id",
            "opponent_id",
        ):
            value = getattr(self, key)
            if value is not None:
                d[key] = round(value, 3) if isinstance(value, float) else value
        if self.extra:
            d["extra"] = self.extra
        return d
