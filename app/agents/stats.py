"""Stats agent: live aggregation of match events into football statistics.

Owns the ``stats`` slice of the shared match state. Produces *signals* (threshold-triggered
player tags and milestones) that downstream agents consume.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from ..models import PITCH_LENGTH, EventType, MatchEvent, MatchMeta

SPRINT_TAG_KMH = 32.5
SHOT_SPEED_TAG_KMH = 105.0
ELITE_PASS_PDR = 8.5
DISTANCE_MILESTONES_KM = (5, 10)
TEAM_PASS_MILESTONES = (100, 200, 300, 400)
MOMENTUM_HALF_LIFE_S = 240.0


def pass_difficulty(distance: float, pressure: float, progress: float, into_final_third: bool) -> float:
    """Pass difficulty rating, 0 (trivial) to 10 (exceptional).

    Combines length, defensive pressure on the passer, forward progression and whether the
    pass breaks into the final third.
    """
    score = (
        1.0
        + min(distance, 60.0) / 60.0 * 3.2
        + pressure * 2.6
        + max(0.0, progress) / 30.0 * 2.0
        + (1.2 if into_final_third else 0.0)
    )
    return round(max(0.0, min(10.0, score)), 2)


@dataclass
class TeamStats:
    passes: int = 0
    completed: int = 0
    pdr_sum: float = 0.0
    shots: int = 0
    on_target: int = 0
    goals: int = 0
    xg: float = 0.0
    possession_s: float = 0.0
    pressures: int = 0
    tackles: int = 0
    interceptions: int = 0
    final_third_entries: int = 0
    ball_speed_sum: float = 0.0
    top_shot_speed: float = 0.0
    possessions: int = 0

    def to_dict(self, total_possession_s: float) -> dict[str, Any]:
        return {
            "possession_pct": round(100 * self.possession_s / total_possession_s, 1)
            if total_possession_s
            else 50.0,
            "passes": self.passes,
            "passes_completed": self.completed,
            "pass_accuracy_pct": round(100 * self.completed / self.passes, 1) if self.passes else 0.0,
            "avg_pass_difficulty": round(self.pdr_sum / self.passes, 2) if self.passes else 0.0,
            "avg_ball_speed_kmh": round(self.ball_speed_sum / self.passes, 1) if self.passes else 0.0,
            "shots": self.shots,
            "shots_on_target": self.on_target,
            "goals": self.goals,
            "xg": round(self.xg, 2),
            "pressures": self.pressures,
            "tackles": self.tackles,
            "interceptions": self.interceptions,
            "final_third_entries": self.final_third_entries,
            "top_shot_speed_kmh": round(self.top_shot_speed, 1),
            "possessions": self.possessions,
        }


@dataclass
class PlayerStats:
    passes: int = 0
    completed: int = 0
    pdr_sum: float = 0.0
    elite_passes: int = 0
    shots: int = 0
    goals: int = 0
    xg: float = 0.0
    tackles: int = 0
    interceptions: int = 0
    pressures: int = 0
    distance_m: float = 0.0
    top_speed_kmh: float = 0.0
    top_shot_speed_kmh: float = 0.0
    touches: int = 0
    tagged_speed: float = 0.0
    distance_milestones: set[int] = field(default_factory=set)

    def rating(self) -> float:
        """Simple 0-10 match rating used for player-of-the-match."""
        acc = self.completed / self.passes if self.passes else 0.0
        r = (
            6.0
            + self.goals * 0.9
            + self.xg * 0.5
            + self.elite_passes * 0.2
            + (acc - 0.8) * 3 * min(1.0, self.passes / 30)
            + (self.tackles + self.interceptions) * 0.12
            + max(0.0, self.distance_m / 1000 - 10) * 0.2
        )
        return round(max(3.0, min(9.9, r)), 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passes": self.passes,
            "passes_completed": self.completed,
            "pass_accuracy_pct": round(100 * self.completed / self.passes, 1) if self.passes else 0.0,
            "avg_pass_difficulty": round(self.pdr_sum / self.passes, 2) if self.passes else 0.0,
            "elite_passes": self.elite_passes,
            "shots": self.shots,
            "goals": self.goals,
            "xg": round(self.xg, 2),
            "tackles": self.tackles,
            "interceptions": self.interceptions,
            "pressures": self.pressures,
            "distance_km": round(self.distance_m / 1000, 2),
            "top_speed_kmh": round(self.top_speed_kmh, 1),
            "top_shot_speed_kmh": round(self.top_shot_speed_kmh, 1),
            "touches": self.touches,
            "rating": self.rating(),
        }


@dataclass
class Signal:
    """Something the Stats agent noticed that downstream agents may narrate."""

    kind: str  # tag_sprint | tag_shot_speed | milestone_distance | milestone_passes | elite_pass
    t: float
    seq: int
    team_id: str | None
    player_id: str | None
    params: dict[str, Any]
    significance: float


@dataclass
class MinuteBucket:
    passes: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    completed: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    possessions: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    turnovers: int = 0
    pressures: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    entries: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    possession_s: dict[str, float] = field(default_factory=lambda: defaultdict(float))


@dataclass
class StatsUpdate:
    event: MatchEvent
    signals: list[Signal]
    pass_difficulty: float | None
    momentum: float
    score: dict[str, int]


class StatsAgent:
    name = "stats"

    def __init__(self, meta: MatchMeta) -> None:
        self.meta = meta
        self.teams = {meta.home.id: TeamStats(), meta.away.id: TeamStats()}
        self.players = {p.id: PlayerStats() for p in meta.all_players()}
        self.score = {meta.home.id: 0, meta.away.id: 0}
        self.threat = {meta.home.id: 0.0, meta.away.id: 0.0}
        self._threat_t = 0.0
        self.momentum = 0.0  # -100 (away dominant) .. +100 (home dominant)
        self.momentum_by_minute: dict[int, float] = {}
        self.buckets: dict[int, MinuteBucket] = defaultdict(MinuteBucket)
        self._poss_team: str | None = None
        self._poss_since = 0.0
        self._poss_passes = 0
        self.current_possession: dict[str, Any] = {"team_id": None, "passes": 0, "start_t": 0.0}
        self.last_t = 0.0
        self.goals: list[dict[str, Any]] = []
        self.recent_passes: deque[MatchEvent] = deque(maxlen=60)

    # ------------------------------------------------------------- helpers
    def _frame_x(self, team_id: str, x: float) -> float:
        return x if team_id == self.meta.home.id else PITCH_LENGTH - x

    def _decay(self, t: float) -> None:
        dt = max(0.0, t - self._threat_t)
        if dt:
            f = math.pow(0.5, dt / MOMENTUM_HALF_LIFE_S)
            for k in self.threat:
                self.threat[k] *= f
            self._threat_t = t

    def _add_threat(self, team_id: str, amount: float) -> None:
        self.threat[team_id] += amount

    def _update_momentum(self) -> None:
        h, a = self.threat[self.meta.home.id], self.threat[self.meta.away.id]
        self.momentum = round(100 * (h - a) / (h + a + 1.5), 1)

    def _start_possession(self, team_id: str, t: float) -> None:
        if self._poss_team is not None and self._poss_team != team_id:
            self.buckets[int(t // 60)].turnovers += 1
        self._close_possession_time(t)
        self._poss_team = team_id
        self._poss_since = t
        self.teams[team_id].possessions += 1
        self.buckets[int(t // 60)].possessions[team_id] += 1
        self.current_possession = {"team_id": team_id, "passes": 0, "start_t": t}

    def _close_possession_time(self, t: float) -> None:
        if self._poss_team is not None and t > self._poss_since:
            dt = t - self._poss_since
            self.teams[self._poss_team].possession_s += dt
            self.buckets[int(self._poss_since // 60)].possession_s[self._poss_team] += dt
            self._poss_since = t

    # ------------------------------------------------------------- ingest
    def ingest(self, ev: MatchEvent) -> StatsUpdate:  # noqa: C901 - event dispatch
        signals: list[Signal] = []
        pdr: float | None = None
        self._decay(ev.t)
        self.last_t = ev.t
        bucket = self.buckets[int(ev.t // 60)]

        if ev.type in (EventType.KICKOFF, EventType.POSSESSION_CHANGE) and ev.team_id:
            self._start_possession(ev.team_id, ev.t)
        elif ev.type in (EventType.HALF_TIME, EventType.FULL_TIME):
            self._close_possession_time(ev.t)
            self._poss_team = None
        else:
            self._close_possession_time(ev.t)

        if ev.type == EventType.PASS and ev.team_id and ev.player_id:
            ts, ps = self.teams[ev.team_id], self.players[ev.player_id]
            fx0 = self._frame_x(ev.team_id, ev.x or 0)
            fx1 = self._frame_x(ev.team_id, ev.end_x or 0)
            into_f3 = fx1 >= 70 and fx0 < 70
            pdr = pass_difficulty(ev.distance_m or 0, ev.pressure or 0, fx1 - fx0, into_f3)
            ts.passes += 1
            ts.pdr_sum += pdr
            ts.ball_speed_sum += ev.speed_kmh or 0
            ps.passes += 1
            ps.pdr_sum += pdr
            ps.touches += 1
            bucket.passes[ev.team_id] += 1
            self.recent_passes.append(ev)
            if ev.outcome == "complete":
                ts.completed += 1
                ps.completed += 1
                bucket.completed[ev.team_id] += 1
                self.current_possession["passes"] += 1
                if into_f3:
                    ts.final_third_entries += 1
                    bucket.entries[ev.team_id] += 1
                    self._add_threat(ev.team_id, 0.7)
                else:
                    self._add_threat(ev.team_id, 0.05)
                if pdr >= ELITE_PASS_PDR:
                    ps.elite_passes += 1
                    signals.append(
                        Signal(
                            "elite_pass",
                            ev.t,
                            ev.seq,
                            ev.team_id,
                            ev.player_id,
                            {
                                "pdr": round(pdr, 1),
                                "receiver_id": ev.receiver_id,
                                "dist": ev.distance_m,
                                "pressure_pct": int(round((ev.pressure or 0) * 100)),
                                "success_probability": ev.extra.get("success_probability"),
                            },
                            significance=min(0.7, 0.5 + (pdr - ELITE_PASS_PDR) * 0.15),
                        )
                    )
                if ts.completed in TEAM_PASS_MILESTONES:
                    signals.append(
                        Signal(
                            "milestone_passes",
                            ev.t,
                            ev.seq,
                            ev.team_id,
                            None,
                            {"count": ts.completed, "acc": round(100 * ts.completed / ts.passes, 1)},
                            significance=0.4,
                        )
                    )

        elif ev.type == EventType.CARRY and ev.team_id and ev.player_id:
            self.players[ev.player_id].touches += 1
            fx0 = self._frame_x(ev.team_id, ev.x or 0)
            fx1 = self._frame_x(ev.team_id, ev.end_x or 0)
            if fx1 >= 70 > fx0:
                self.teams[ev.team_id].final_third_entries += 1
                bucket.entries[ev.team_id] += 1
                self._add_threat(ev.team_id, 0.5)

        elif ev.type == EventType.SHOT and ev.team_id and ev.player_id:
            ts, ps = self.teams[ev.team_id], self.players[ev.player_id]
            ts.shots += 1
            ps.shots += 1
            ps.touches += 1
            ts.xg += ev.xg or 0
            ps.xg += ev.xg or 0
            speed = ev.speed_kmh or 0
            ts.top_shot_speed = max(ts.top_shot_speed, speed)
            if ev.outcome in ("goal", "saved"):
                ts.on_target += 1
            self._add_threat(ev.team_id, 1.0 + (ev.xg or 0) * 6)
            if speed >= SHOT_SPEED_TAG_KMH and speed > ps.top_shot_speed_kmh:
                signals.append(
                    Signal(
                        "tag_shot_speed",
                        ev.t,
                        ev.seq,
                        ev.team_id,
                        ev.player_id,
                        {"speed": round(speed, 1), "threshold": SHOT_SPEED_TAG_KMH},
                        significance=0.55,
                    )
                )
            ps.top_shot_speed_kmh = max(ps.top_shot_speed_kmh, speed)
            if ev.outcome == "goal":
                ts.goals += 1
                ps.goals += 1
                self.score[ev.team_id] += 1
                self.goals.append(
                    {
                        "t": ev.t,
                        "minute": ev.minute + 1,
                        "team_id": ev.team_id,
                        "player_id": ev.player_id,
                        "xg": ev.xg,
                    }
                )

        elif ev.type == EventType.PRESSURE and ev.team_id and ev.player_id:
            self.teams[ev.team_id].pressures += 1
            self.players[ev.player_id].pressures += 1
            bucket.pressures[ev.team_id] += 1
            # Pressing in the opponent's half (high press) builds momentum.
            fx = self._frame_x(ev.team_id, ev.x or 0)
            self._add_threat(ev.team_id, 0.12 if fx > 52.5 else 0.03)

        elif ev.type == EventType.TACKLE and ev.team_id and ev.player_id:
            self.teams[ev.team_id].tackles += 1
            self.players[ev.player_id].tackles += 1
            self._add_threat(ev.team_id, 0.3)

        elif ev.type == EventType.INTERCEPTION and ev.team_id and ev.player_id:
            self.teams[ev.team_id].interceptions += 1
            self.players[ev.player_id].interceptions += 1
            self._add_threat(ev.team_id, 0.15)

        elif ev.type == EventType.SPRINT and ev.player_id:
            ps = self.players[ev.player_id]
            speed = ev.speed_kmh or 0
            ps.top_speed_kmh = max(ps.top_speed_kmh, speed)
            if speed >= SPRINT_TAG_KMH and speed > ps.tagged_speed + 0.3:
                ps.tagged_speed = speed
                signals.append(
                    Signal(
                        "tag_sprint",
                        ev.t,
                        ev.seq,
                        ev.team_id,
                        ev.player_id,
                        {"speed": round(speed, 1), "threshold": SPRINT_TAG_KMH},
                        significance=0.5,
                    )
                )

        elif ev.type == EventType.TRACKING:
            dist = ev.extra.get("distance_m", {})
            for pid, metres in dist.items():
                if pid not in self.players:
                    continue
                ps = self.players[pid]
                ps.distance_m = float(metres)
                for km in DISTANCE_MILESTONES_KM:
                    if ps.distance_m >= km * 1000 and km not in ps.distance_milestones:
                        ps.distance_milestones.add(km)
                        if km == DISTANCE_MILESTONES_KM[-1]:
                            player = self.meta.find_player(pid)
                            signals.append(
                                Signal(
                                    "milestone_distance",
                                    ev.t,
                                    ev.seq,
                                    player.team_id if player else None,
                                    pid,
                                    {"km": km},
                                    significance=0.3,
                                )
                            )
            self.momentum_by_minute[ev.mark_minute] = self.momentum

        self._update_momentum()
        return StatsUpdate(ev, signals, pdr, self.momentum, dict(self.score))

    # ------------------------------------------------------------- views
    def window(self, end_minute: int, length: int) -> dict[str, Any]:
        """Aggregate the per-minute buckets for minutes (end-length, end]."""
        mins = range(max(0, end_minute - length + 1), end_minute + 1)
        out: dict[str, Any] = {"minutes": len(mins), "turnovers": 0, "teams": {}}
        for tid in self.teams:
            out["teams"][tid] = {
                "passes": 0,
                "completed": 0,
                "possessions": 0,
                "pressures": 0,
                "entries": 0,
                "possession_s": 0.0,
            }
        for m in mins:
            b = self.buckets.get(m)
            if not b:
                continue
            out["turnovers"] += b.turnovers
            for tid, d in out["teams"].items():
                d["passes"] += b.passes.get(tid, 0)
                d["completed"] += b.completed.get(tid, 0)
                d["possessions"] += b.possessions.get(tid, 0)
                d["pressures"] += b.pressures.get(tid, 0)
                d["entries"] += b.entries.get(tid, 0)
                d["possession_s"] += b.possession_s.get(tid, 0.0)
        return out

    def snapshot(self) -> dict[str, Any]:
        total = sum(t.possession_s for t in self.teams.values())
        return {
            "t": round(self.last_t, 1),
            "score": dict(self.score),
            "momentum": self.momentum,
            "teams": {tid: ts.to_dict(total) for tid, ts in self.teams.items()},
        }

    def player_snapshot(self, player_id: str) -> dict[str, Any]:
        return self.players[player_id].to_dict()

    def top_players(self, n: int = 3) -> list[tuple[str, float]]:
        ranked = sorted(((pid, ps.rating()) for pid, ps in self.players.items()), key=lambda x: (-x[1], x[0]))
        return ranked[:n]
