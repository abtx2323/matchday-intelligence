"""Simulator agent: seeded, deterministic synthetic football match generator.

The same seed always yields the identical match (teams, events, coordinates and outcomes),
which makes every downstream agent reproducible and testable.
"""

from __future__ import annotations

import asyncio
import math
import random
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

from .clubs import CLUBS, FORMATION_433, VENUES
from .models import (
    HALF_SECONDS,
    PITCH_LENGTH,
    PITCH_WIDTH,
    EventType,
    MatchEvent,
    MatchMeta,
    Player,
    Team,
)

GOAL_X = PITCH_LENGTH
GOAL_Y = PITCH_WIDTH / 2
GOAL_WIDTH = 7.32


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def shot_xg(x: float, y: float, pressure: float = 0.0) -> float:
    """Expected goals for a shot from (x, y) in the attacking frame."""
    dx = max(GOAL_X - x, 0.5)
    dy = abs(GOAL_Y - y)
    dist = math.hypot(dx, dy)
    denom = dx * dx + dy * dy - (GOAL_WIDTH / 2) ** 2
    angle = math.atan2(GOAL_WIDTH * dx, denom)
    if angle < 0:
        angle += math.pi
    logit = -1.75 + 1.6 * angle - 0.08 * dist
    xg = 1 / (1 + math.exp(-logit))
    return round(_clamp(xg * (1 - 0.35 * pressure), 0.01, 0.95), 3)


def _build_team(club: dict, rng: random.Random, mirror: bool) -> Team:
    players: list[Player] = []
    for (name, number), (pos, bx, by) in zip(club["players"], FORMATION_433, strict=True):
        is_gk = pos == "GK"
        players.append(
            Player(
                id=f"{club['id']}-{number}",
                name=name,
                number=number,
                position=pos,
                team_id=club["id"],
                base_x=bx,
                base_y=by,
                skill=round(_clamp(rng.gauss(0.72, 0.08), 0.5, 0.95), 3),
                pace=round(_clamp(rng.gauss(31.0 if not is_gk else 25.0, 1.6), 24.0, 36.5), 2),
                work_rate=round(_clamp(rng.gauss(10.8 if not is_gk else 5.2, 0.7), 4.5, 13.2), 2),
            )
        )
    _ = mirror  # positions are stored in the attacking frame; mirroring happens on output.
    return Team(
        id=club["id"],
        name=club["name"],
        short=club["short"],
        color=club["color"],
        style=club["style"],
        strength=club["strength"],
        players=tuple(players),
    )


@dataclass
class _Ball:
    x: float
    y: float


class MatchSimulator:
    """Generates one synthetic match. Home attacks towards x=105, away towards x=0."""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self._rng = random.Random(seed)
        clubs = list(CLUBS)
        self._rng.shuffle(clubs)
        home = _build_team(clubs[0], self._rng, mirror=False)
        away = _build_team(clubs[1], self._rng, mirror=True)
        self.meta = MatchMeta(
            match_id=f"MDI-{seed}",
            seed=seed,
            competition="Synthetic Premier Showcase (fictional)",
            venue=self._rng.choice(VENUES),
            kickoff_local=f"{self._rng.choice([12, 15, 17, 20])}:{self._rng.choice(['00', '30'])}",
            home=home,
            away=away,
        )
        self._events: list[MatchEvent] | None = None

    # ------------------------------------------------------------------ helpers
    def _to_abs(self, team_id: str, x: float, y: float) -> tuple[float, float]:
        if team_id == self.meta.home.id:
            return round(x, 2), round(y, 2)
        return round(PITCH_LENGTH - x, 2), round(PITCH_WIDTH - y, 2)

    @staticmethod
    def _mirror(x: float, y: float) -> tuple[float, float]:
        return PITCH_LENGTH - x, PITCH_WIDTH - y

    @staticmethod
    def _live_pos(p: Player, ball_x: float, attacking: bool) -> tuple[float, float]:
        """Player position in its own attacking frame, with the block shifting towards the ball."""
        shift = (ball_x - 52.5) * (0.35 if attacking else 0.3)
        if p.position == "GK":
            shift *= 0.15
        x = _clamp(p.base_x + shift + (6 if attacking else -4), 1.0, PITCH_LENGTH - 1)
        return x, p.base_y

    # ------------------------------------------------------------------ generation
    def events(self) -> list[MatchEvent]:
        if self._events is None:
            self._events = list(self._generate())
        return self._events

    def _generate(self) -> Iterator[MatchEvent]:  # noqa: C901 - one readable state machine
        rng = self._rng
        meta = self.meta
        seq = 0
        score = {meta.home.id: 0, meta.away.id: 0}
        distance = {p.id: 0.0 for p in meta.all_players()}
        next_tracking = 60.0

        last_t = 0.0
        p2_offset = 0.0

        def emit(**kw) -> MatchEvent:
            nonlocal seq, last_t
            seq += 1
            kw["t"] = max(float(kw["t"]), last_t)  # feed time never goes backwards
            last_t = kw["t"]
            clock_s = kw["t"] if kw["period"] == 1 else kw["t"] - p2_offset
            return MatchEvent(seq=seq, clock_s=round(clock_s, 3), **kw)

        def team_obj(team_id: str) -> Team:
            return meta.team(team_id)

        def outfield(team: Team) -> list[Player]:
            return [p for p in team.players if p.position != "GK"]

        def by_pos(team: Team, pos: str) -> Player:
            return next(p for p in team.players if p.position == pos)

        def nearest(team: Team, fx: float, fy: float, frame_attacking: bool) -> Player:
            # fx, fy are in the *opponent's* attacking frame; convert to this team's frame.
            tx, ty = self._mirror(fx, fy)
            best, best_d = None, 1e9
            for p in outfield(team):
                px, py = self._live_pos(p, tx, frame_attacking)
                d = math.hypot(px - tx, py - ty)
                if d < best_d:
                    best, best_d = p, d
            if best is None:  # pragma: no cover - a team always has outfield players
                raise RuntimeError("team has no outfield players")
            return best

        stoppage = [rng.randint(60, 200), rng.randint(120, 330)]
        period = 1
        t = 0.0
        period_end = HALF_SECONDS + stoppage[0]
        poss = meta.home.id
        carrier = by_pos(meta.home, "DM")
        ball = _Ball(52.5, 34.0)

        hx, hy = self._to_abs(poss, ball.x, ball.y)
        yield emit(
            t=t,
            period=1,
            type=EventType.KICKOFF,
            team_id=poss,
            player_id=carrier.id,
            x=hx,
            y=hy,
            extra={"score": dict(score), "period_start": True},
        )

        while True:
            # Distance-covered tracking snapshots once per match minute.
            while t >= next_tracking:
                for p in meta.all_players():
                    distance[p.id] += p.work_rate * 1000 / 90 * rng.uniform(0.85, 1.15)
                yield emit(
                    t=next_tracking,
                    period=period,
                    type=EventType.TRACKING,
                    extra={
                        "minute_mark": int(next_tracking // 60),
                        "distance_m": {k: round(v, 1) for k, v in distance.items()},
                    },
                )
                next_tracking += 60.0

            if t >= period_end:
                if period == 1:
                    yield emit(t=t, period=1, type=EventType.HALF_TIME, extra={"score": dict(score)})
                    period = 2
                    p2_offset = t - HALF_SECONDS  # second half clock restarts at 45:00
                    period_end = t + HALF_SECONDS + stoppage[1]
                    poss = meta.away.id
                    carrier = by_pos(meta.away, "DM")
                    ball = _Ball(52.5, 34.0)
                    hx, hy = self._to_abs(poss, ball.x, ball.y)
                    yield emit(
                        t=t,
                        period=2,
                        type=EventType.KICKOFF,
                        team_id=poss,
                        player_id=carrier.id,
                        x=hx,
                        y=hy,
                        extra={"score": dict(score), "period_start": True},
                    )
                    continue
                yield emit(t=t, period=2, type=EventType.FULL_TIME, extra={"score": dict(score)})
                return

            att = team_obj(poss)
            dfn = meta.opponent(poss)
            zone = ball.x / PITCH_LENGTH

            # Occasional off-ball sprint (drives threshold-triggered speed tags).
            if rng.random() < 0.06:
                runner = rng.choice(outfield(rng.choice([att, dfn])))
                top = round(runner.pace * rng.uniform(0.84, 1.0), 2)
                rx, ry = self._live_pos(
                    runner,
                    ball.x if runner.team_id == poss else PITCH_LENGTH - ball.x,
                    runner.team_id == poss,
                )
                ax, ay = self._to_abs(runner.team_id, rx, ry)
                yield emit(
                    t=t,
                    period=period,
                    type=EventType.SPRINT,
                    team_id=runner.team_id,
                    player_id=runner.id,
                    x=ax,
                    y=ay,
                    speed_kmh=top,
                    distance_m=round(rng.uniform(15, 45), 1),
                )

            # Defensive pressure on the ball carrier.
            press_base = {"pressing": 0.5, "direct": 0.3, "possession": 0.34}[dfn.style]
            pressure = 0.0
            if rng.random() < press_base * (0.6 + zone * 0.7):
                pressure = round(rng.uniform(0.3, 1.0), 2)
                presser = nearest(dfn, ball.x, ball.y, frame_attacking=False)
                ax, ay = self._to_abs(poss, ball.x, ball.y)
                yield emit(
                    t=t,
                    period=period,
                    type=EventType.PRESSURE,
                    team_id=dfn.id,
                    player_id=presser.id,
                    opponent_id=carrier.id,
                    x=ax,
                    y=ay,
                    pressure=pressure,
                )

            # Tackle attempt.
            tackle_p = 0.035 + pressure * 0.12 - (carrier.skill - 0.7) * 0.08
            if pressure and rng.random() < tackle_p:
                tackler = nearest(dfn, ball.x, ball.y, frame_attacking=False)
                ax, ay = self._to_abs(poss, ball.x, ball.y)
                yield emit(
                    t=t,
                    period=period,
                    type=EventType.TACKLE,
                    team_id=dfn.id,
                    player_id=tackler.id,
                    opponent_id=carrier.id,
                    x=ax,
                    y=ay,
                    outcome="won",
                    pressure=pressure,
                )
                t += rng.uniform(1.0, 2.5)
                yield emit(
                    t=t,
                    period=period,
                    type=EventType.POSSESSION_CHANGE,
                    team_id=dfn.id,
                    player_id=tackler.id,
                    x=ax,
                    y=ay,
                    extra={"reason": "tackle"},
                )
                poss, carrier = dfn.id, tackler
                ball.x, ball.y = self._mirror(ball.x, ball.y)
                continue

            # Shot decision.
            dist_goal = math.hypot(GOAL_X - ball.x, GOAL_Y - ball.y)
            shot_p = 0.0
            if dist_goal < 30:
                shot_p = 0.03 + (30 - dist_goal) / 30 * 0.26
                if carrier.position in ("ST", "LW", "RW"):
                    shot_p *= 1.3
            if rng.random() < shot_p:
                xg = shot_xg(ball.x, ball.y, pressure)
                speed = round(_clamp(rng.gauss(72 + carrier.skill * 38 - dist_goal * 0.2, 9), 42, 132), 1)
                finishing = 0.8 + (carrier.skill - 0.7) * 1.2
                r = rng.random()
                if r < xg * finishing:
                    outcome = "goal"
                elif r < xg * finishing + 0.28:
                    outcome = "saved"
                elif r < xg * finishing + 0.5:
                    outcome = "blocked"
                else:
                    outcome = "off_target"
                ax, ay = self._to_abs(poss, ball.x, ball.y)
                gx, gy = self._to_abs(poss, GOAL_X, GOAL_Y + rng.uniform(-3.4, 3.4))
                if outcome == "goal":
                    score[poss] += 1
                yield emit(
                    t=t,
                    period=period,
                    type=EventType.SHOT,
                    team_id=poss,
                    player_id=carrier.id,
                    x=ax,
                    y=ay,
                    end_x=gx,
                    end_y=gy,
                    distance_m=round(dist_goal, 1),
                    outcome=outcome,
                    pressure=pressure,
                    speed_kmh=speed,
                    xg=xg,
                    extra={"score": dict(score)},
                )
                if outcome == "goal":
                    t += rng.uniform(50, 80)
                    poss = dfn.id
                    carrier = by_pos(dfn, "DM")
                    ball = _Ball(52.5, 34.0)
                    hx, hy = self._to_abs(poss, ball.x, ball.y)
                    yield emit(
                        t=t,
                        period=period,
                        type=EventType.KICKOFF,
                        team_id=poss,
                        player_id=carrier.id,
                        x=hx,
                        y=hy,
                        extra={"score": dict(score)},
                    )
                    continue
                if outcome == "blocked" and rng.random() < 0.45:
                    t += rng.uniform(2, 4)
                    ball.x = _clamp(ball.x - rng.uniform(4, 10), 40, 100)
                    continue
                t += rng.uniform(8, 20)
                poss = dfn.id
                carrier = by_pos(dfn, "GK")
                ball = _Ball(6.0, 34.0)
                gx2, gy2 = self._to_abs(poss, ball.x, ball.y)
                yield emit(
                    t=t,
                    period=period,
                    type=EventType.POSSESSION_CHANGE,
                    team_id=poss,
                    player_id=carrier.id,
                    x=gx2,
                    y=gy2,
                    extra={"reason": f"shot_{outcome}"},
                )
                continue

            # Carry.
            if rng.random() < 0.17 and carrier.position != "GK":
                gain = rng.uniform(4, 14)
                nx = _clamp(ball.x + gain, 1, PITCH_LENGTH - 2)
                ny = _clamp(ball.y + rng.uniform(-6, 6), 2, PITCH_WIDTH - 2)
                ax, ay = self._to_abs(poss, ball.x, ball.y)
                ex, ey = self._to_abs(poss, nx, ny)
                dur = rng.uniform(2.5, 5.5)
                yield emit(
                    t=t,
                    period=period,
                    type=EventType.CARRY,
                    team_id=poss,
                    player_id=carrier.id,
                    x=ax,
                    y=ay,
                    end_x=ex,
                    end_y=ey,
                    distance_m=round(math.hypot(nx - ball.x, ny - ball.y), 1),
                    speed_kmh=round(math.hypot(nx - ball.x, ny - ball.y) / dur * 3.6, 1),
                    pressure=pressure,
                )
                ball.x, ball.y = nx, ny
                t += dur
                continue

            # Pass.
            forward_bias = {"direct": 1.6, "pressing": 1.1, "possession": 0.7}[att.style]
            candidates: list[tuple[Player, float, float, float]] = []
            for p in att.players:
                if p.id == carrier.id:
                    continue
                px, py = self._live_pos(p, ball.x, attacking=True)
                d = math.hypot(px - ball.x, py - ball.y)
                w = math.exp(-d / 18) * (1 + forward_bias * max(0.0, px - ball.x) / 20)
                if p.position == "GK":
                    w *= 0.15
                candidates.append((p, w, px, py))
            total = sum(c[1] for c in candidates)
            pick = rng.uniform(0, total)
            acc = 0.0
            receiver, rx, ry = candidates[-1][0], candidates[-1][2], candidates[-1][3]
            for p, w, px, py in candidates:
                acc += w
                if pick <= acc:
                    receiver, rx, ry = p, px, py
                    break
            tx = _clamp(rx + rng.gauss(0, 3), 1, PITCH_LENGTH - 1)
            ty = _clamp(ry + rng.gauss(0, 3), 1, PITCH_WIDTH - 1)
            dist = math.hypot(tx - ball.x, ty - ball.y)
            progress = tx - ball.x
            p_ok = (
                0.995
                - dist * 0.0045
                - pressure * 0.16
                - max(0.0, progress) * 0.0025
                + (carrier.skill - 0.7) * 0.25
                + (att.strength - 0.55) * 0.3
            )
            p_ok = _clamp(p_ok, 0.35, 0.97)
            ok = rng.random() < p_ok
            flight = 0.5 + dist / 22
            ball_speed = round(_clamp(dist / flight * 3.6 * rng.uniform(0.9, 1.12), 12, 110), 1)
            ax, ay = self._to_abs(poss, ball.x, ball.y)
            ex, ey = self._to_abs(poss, tx, ty)
            yield emit(
                t=t,
                period=period,
                type=EventType.PASS,
                team_id=poss,
                player_id=carrier.id,
                receiver_id=receiver.id,
                x=ax,
                y=ay,
                end_x=ex,
                end_y=ey,
                distance_m=round(dist, 1),
                outcome="complete" if ok else "incomplete",
                pressure=pressure,
                speed_kmh=ball_speed,
                extra={"success_probability": round(p_ok, 3)},
            )
            t += 1.2 + dist / 16 + rng.uniform(0.4, 2.8)
            if ok:
                carrier = receiver
                ball.x, ball.y = tx, ty
                continue
            # Turnover: interception or ball out of play.
            if rng.random() < 0.7:
                interceptor = nearest(dfn, tx, ty, frame_attacking=False)
                yield emit(
                    t=t,
                    period=period,
                    type=EventType.INTERCEPTION,
                    team_id=dfn.id,
                    player_id=interceptor.id,
                    opponent_id=carrier.id,
                    x=ex,
                    y=ey,
                )
                reason = "interception"
            else:
                interceptor = nearest(dfn, tx, ty, frame_attacking=False)
                reason = "out_of_play"
                t += rng.uniform(4, 9)
            yield emit(
                t=t,
                period=period,
                type=EventType.POSSESSION_CHANGE,
                team_id=dfn.id,
                player_id=interceptor.id,
                x=ex,
                y=ey,
                extra={"reason": reason},
            )
            poss, carrier = dfn.id, interceptor
            ball.x, ball.y = self._mirror(tx, ty)

    # ------------------------------------------------------------------ streaming
    async def stream(self, speed: float = 30.0, start_t: float = 0.0) -> AsyncIterator[MatchEvent]:
        """Yield events paced in real time. ``speed`` is match seconds per wall-clock second.

        Events before ``start_t`` are yielded immediately (so downstream state can catch up).
        A speed of 0 means "as fast as possible".
        """
        prev_t: float | None = None
        for ev in self.events():
            if ev.t > start_t and speed > 0 and prev_t is not None:
                gap = max(0.0, ev.t - max(prev_t, start_t))
                if gap > 0:
                    await asyncio.sleep(gap / speed)
            prev_t = ev.t
            yield ev
