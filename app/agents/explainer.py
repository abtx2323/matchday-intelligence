"""Explainer agent: decides which moments matter and *why*, attaching the evidence.

It reads the Stats agent's state (never raw text), and outputs structured ``Insight`` objects
with reason codes plus the numbers that justify them. Text is produced later, per viewer, by the
Storyteller/Personalizer handoff, so the reasoning is language-independent and auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import EventType, MatchMeta
from .stats import StatsAgent, StatsUpdate

WINDOW_MIN = 5
SWING_DELTA = 35.0
SWING_COOLDOWN_MIN = 10
RHYTHM_COOLDOWN_MIN = 8
PRESS_COOLDOWN_MIN = 10
BIG_CHANCE_XG = 0.28
CONSOLATION_MINUTE = 75


@dataclass
class Insight:
    id: str
    kind: str
    t: float
    seq: int
    team_id: str | None
    player_id: str | None
    significance: float
    params: dict[str, Any]
    reasons: list[tuple[str, dict[str, Any]]]
    evidence: dict[str, Any]
    anchor: tuple[float, float] | None = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "t": self.t,
            "team_id": self.team_id,
            "player_id": self.player_id,
            "significance": self.significance,
            "reasons": [c for c, _ in self.reasons],
            "evidence": self.evidence,
        }


class ExplainerAgent:
    name = "explainer"

    def __init__(self, meta: MatchMeta, stats: StatsAgent) -> None:
        self.meta = meta
        self.stats = stats
        self._prev_momentum = 0.0
        self._last_swing = -99
        self._last_rhythm = -99
        self._rhythm_state = "balanced"
        self._last_press: dict[str, int] = {meta.home.id: -99, meta.away.id: -99}
        self.insights: list[Insight] = []

    def _persp(self, team_id: str, momentum: float) -> float:
        return momentum if team_id == self.meta.home.id else -momentum

    def analyze(self, upd: StatsUpdate) -> list[Insight]:
        ev = upd.event
        out: list[Insight] = []
        if ev.type == EventType.SHOT and ev.team_id and ev.player_id:
            out.extend(self._shot(upd))
        for sig in upd.signals:
            if sig.kind == "elite_pass":
                out.append(self._elite_pass(sig, upd))
        if ev.type == EventType.TRACKING and ev.mark_minute >= WINDOW_MIN:
            out.extend(self._phase_checks(ev.mark_minute, ev.t, ev.seq))
        self._prev_momentum = upd.momentum
        for ins in out:
            ins.params.setdefault("minute", ev.minute + 1)
        self.insights.extend(out)
        return out

    # ----------------------------------------------------------------- shots
    def _shot(self, upd: StatsUpdate) -> list[Insight]:
        ev = upd.event
        team_id = ev.team_id or ""
        opp = self.meta.opponent(team_id).id
        xg = round(ev.xg or 0.0, 2)
        momentum_before = round(self._persp(team_id, self._prev_momentum), 1)
        poss = self.stats.current_possession
        build_passes = poss.get("passes", 0) if poss.get("team_id") == team_id else 0
        build_secs = int(ev.t - poss.get("start_t", ev.t)) if build_passes else 0
        evidence = {
            "xg": xg,
            "shot_speed_kmh": ev.speed_kmh,
            "distance_m": ev.distance_m,
            "pressure": ev.pressure,
            "momentum_before": momentum_before,
            "buildup_passes": build_passes,
            "buildup_seconds": build_secs,
            "team_xg_total": round(self.stats.teams[team_id].xg, 2),
            "score": dict(upd.score),
        }
        anchor = (ev.x or 0.0, ev.y or 0.0)
        if ev.outcome == "goal":
            diff_before = (upd.score[team_id] - 1) - upd.score[opp]
            if diff_before == 0:
                state = "go_ahead"
            elif diff_before == -1:
                state = "equaliser"
            elif diff_before >= 1:
                state = "extends_lead"
            elif ev.period == 2 and ev.match_clock_s >= CONSOLATION_MINUTE * 60:
                state = "consolation"  # only a consolation when there's little time left
            else:
                state = "pulls_back"
            reasons: list[tuple[str, dict[str, Any]]] = [(state, {})]
            if momentum_before <= -20:
                reasons.append(("against_run", {"momentum": abs(momentum_before)}))
            elif momentum_before >= 25:
                reasons.append(("with_run", {"momentum": momentum_before}))
            if build_passes >= 4:
                reasons.append(("buildup", {"passes": build_passes, "secs": build_secs}))
            if xg < 0.08:
                reasons.append(("low_xg", {"xg": xg}))
            elif xg >= 0.35:
                reasons.append(("high_xg", {"xg": xg}))
            sig = 0.9 + (0.05 if state in ("go_ahead", "equaliser") else 0.0)
            if ev.period == 2 and ev.match_clock_s >= 80 * 60:
                sig = min(1.0, sig + 0.05)
            return [
                Insight(
                    id=f"ins-{ev.seq}-goal",
                    kind="goal",
                    t=ev.t,
                    seq=ev.seq,
                    team_id=team_id,
                    player_id=ev.player_id,
                    significance=round(sig, 2),
                    params={"xg": xg},
                    reasons=reasons,
                    evidence=evidence,
                    anchor=anchor,
                    tags=[state],
                )
            ]
        if xg >= BIG_CHANCE_XG:
            return [
                Insight(
                    id=f"ins-{ev.seq}-big_chance",
                    kind="big_chance",
                    t=ev.t,
                    seq=ev.seq,
                    team_id=team_id,
                    player_id=ev.player_id,
                    significance=round(min(0.85, 0.55 + xg * 0.4), 2),
                    params={"xg": xg, "team_xg": evidence["team_xg_total"]},
                    reasons=[("miss_cost", {"pct": int(round(xg * 100))})],
                    evidence=evidence,
                    anchor=anchor,
                )
            ]
        return []

    # ----------------------------------------------------------------- passes
    def _elite_pass(self, sig: Any, upd: StatsUpdate) -> Insight:
        ev = upd.event
        prob = sig.params.get("success_probability") or 0.5
        return Insight(
            id=f"ins-{ev.seq}-elite_pass",
            kind="elite_pass",
            t=ev.t,
            seq=ev.seq,
            team_id=sig.team_id,
            player_id=sig.player_id,
            significance=round(sig.significance, 2),
            params=dict(sig.params),
            reasons=[("elite_why", {"pct": int(round(prob * 100))})],
            evidence={
                "pass_difficulty": sig.params["pdr"],
                "distance_m": ev.distance_m,
                "pressure": ev.pressure,
                "completion_probability": prob,
                "ball_speed_kmh": ev.speed_kmh,
            },
            anchor=(ev.end_x or 0.0, ev.end_y or 0.0),
        )

    # ----------------------------------------------------------------- phases
    def _phase_checks(self, minute: int, t: float, seq: int) -> list[Insight]:
        out: list[Insight] = []
        home, away = self.meta.home.id, self.meta.away.id
        last = minute - 1
        win = self.stats.window(last, WINDOW_MIN)
        prev = self.stats.window(last - WINDOW_MIN, WINDOW_MIN)
        teams = win["teams"]

        # 1) Momentum swing.
        now = self.stats.momentum
        before = self.stats.momentum_by_minute.get(minute - WINDOW_MIN)
        if before is not None and minute - self._last_swing >= SWING_COOLDOWN_MIN:
            delta = now - before
            team = home if delta > 0 else away
            if abs(delta) >= SWING_DELTA and self._persp(team, now) >= 15:
                opp = away if team == home else home
                o = teams[opp]
                opp_acc = round(100 * o["completed"] / o["passes"], 1) if o["passes"] else 0.0
                entries_total = teams[home]["entries"] + teams[away]["entries"]
                share = round(100 * teams[team]["entries"] / entries_total) if entries_total else 50
                press_rate = round(teams[team]["pressures"] / WINDOW_MIN, 1)
                self._last_swing = minute
                out.append(
                    Insight(
                        id=f"ins-{seq}-momentum_swing",
                        kind="momentum_swing",
                        t=t,
                        seq=seq,
                        team_id=team,
                        player_id=None,
                        significance=0.72,
                        params={
                            "before": round(self._persp(team, before)),
                            "after": round(self._persp(team, now)),
                            "window": WINDOW_MIN,
                        },
                        reasons=[
                            ("swing_pressure", {"press": press_rate, "acc": opp_acc}),
                            ("swing_territory", {"share": share, "window": WINDOW_MIN}),
                        ],
                        evidence={
                            "momentum_before": before,
                            "momentum_now": now,
                            "team_pressures_per_min": press_rate,
                            "opponent_pass_accuracy_pct": opp_acc,
                            "final_third_entry_share_pct": share,
                            "window_min": WINDOW_MIN,
                        },
                    )
                )

        # 2) Control vs chaos (game rhythm).
        total_completed = teams[home]["completed"] + teams[away]["completed"]
        total_poss = max(1, teams[home]["possessions"] + teams[away]["possessions"])
        ppp = round(total_completed / total_poss, 1)
        turnovers_pm = round(win["turnovers"] / WINDOW_MIN, 1)
        poss_total = teams[home]["possession_s"] + teams[away]["possession_s"] or 1.0
        state, dom = "balanced", None
        for tid in (home, away):
            share = teams[tid]["possession_s"] / poss_total
            team_ppp = teams[tid]["completed"] / max(1, teams[tid]["possessions"])
            if share >= 0.62 and team_ppp >= 4.5:
                state, dom = "control", tid
                ppp = round(team_ppp, 1)
        if state == "balanced" and turnovers_pm >= 3.4 and ppp <= 2.6:
            state = "chaos"
        if (
            state != self._rhythm_state
            and state != "balanced"
            and minute - self._last_rhythm >= RHYTHM_COOLDOWN_MIN
        ):
            self._last_rhythm = minute
            evidence = {
                "passes_per_possession": ppp,
                "turnovers_per_min": turnovers_pm,
                "window_min": WINDOW_MIN,
            }
            if dom:
                evidence["possession_share_pct"] = round(100 * teams[dom]["possession_s"] / poss_total, 1)
            out.append(
                Insight(
                    id=f"ins-{seq}-rhythm_{state}",
                    kind=f"rhythm_{state}",
                    t=t,
                    seq=seq,
                    team_id=dom,
                    player_id=None,
                    significance=0.62,
                    params={"ppp": ppp, "turnovers": turnovers_pm},
                    reasons=[(f"{state}_why", {})],
                    evidence=evidence,
                )
            )
        self._rhythm_state = state

        # 3) Pressing shift.
        for tid in (home, away):
            after = round(teams[tid]["pressures"] / WINDOW_MIN, 1)
            before_p = round(prev["teams"][tid]["pressures"] / WINDOW_MIN, 1)
            if (
                minute >= 2 * WINDOW_MIN
                and after >= 4.0
                and after >= before_p * 1.4
                and minute - self._last_press[tid] >= PRESS_COOLDOWN_MIN
            ):
                self._last_press[tid] = minute
                out.append(
                    Insight(
                        id=f"ins-{seq}-pressure_shift-{tid}",
                        kind="pressure_shift",
                        t=t,
                        seq=seq,
                        team_id=tid,
                        player_id=None,
                        significance=0.58,
                        params={"before": before_p, "after": after},
                        reasons=[("pressure_why", {})],
                        evidence={
                            "pressures_per_min_before": before_p,
                            "pressures_per_min_now": after,
                            "window_min": WINDOW_MIN,
                        },
                    )
                )
        return out
