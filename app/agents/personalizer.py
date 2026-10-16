"""Personalizer agent: shapes the shared intelligence into one viewer's overlay feed.

Same facts, genuinely different products:
- Analyst: dense stat panels every 5', per-minute momentum with history, every insight with full
  evidence, all player tags, pitch markers for shots/tackles/elite passes.
- Casual: only big moments, plain-language one-liners, "who's on top" every 5', few numbers.
- Favorite club: boosts that club's moments, frames momentum from its perspective, fan alerts.
- Player focus: a live player card, every moment involving the player, others dialled down.
- Language: every text in the viewer's language (en/es/fr).
"""

from __future__ import annotations

from typing import Any

from ..models import EventType, MatchEvent, MatchMeta
from ..schema import (
    Anchor,
    Display,
    MatchClock,
    Mode,
    OverlayEvent,
    OverlayType,
    Slot,
)
from . import i18n
from .profile import ViewerProfile
from .stats import StatsAgent, StatsUpdate
from .storyteller import NarrativeItem, StorytellerAgent

ANALYST_THRESHOLD = 0.32
CASUAL_THRESHOLD = 0.6
FOCUS_PENALTY = 0.1
FAV_BOOST = 0.15
FOCUS_BOOST = 0.35


def _clock(ev: MatchEvent) -> MatchClock:
    return MatchClock(t=round(ev.t, 1), period=ev.period, display=ev.clock)


def _anchor(xy: tuple[float, float] | None) -> Anchor | None:
    if not xy or xy[0] is None or xy[1] is None:
        return None
    return Anchor(x=max(0.0, min(105.0, xy[0])), y=max(0.0, min(68.0, xy[1])))


class PersonalizerAgent:
    name = "personalizer"

    def __init__(
        self, meta: MatchMeta, stats: StatsAgent, storyteller: StorytellerAgent, profile: ViewerProfile
    ) -> None:
        self.meta = meta
        self.stats = stats
        self.storyteller = storyteller
        self.profile = profile
        self.analyst = profile.mode == Mode.ANALYST
        self.lang = str(profile.language)
        if profile.favorite_club and profile.favorite_club not in (meta.home.id, meta.away.id):
            raise ValueError("favorite_club is not playing in this match")
        if profile.focus_player and not meta.find_player(profile.focus_player):
            raise ValueError("focus_player is not in this match")

    # ----------------------------------------------------------------- helpers
    def _ov(
        self,
        ev: MatchEvent,
        otype: OverlayType,
        suffix: str,
        slot: Slot,
        *,
        duration_ms: int,
        priority: int,
        significance: float,
        provider: str = "template",
        headline: str | None = None,
        text: str | None = None,
        data: dict[str, Any] | None = None,
        evidence: dict[str, Any] | None = None,
        anchor: Anchor | None = None,
        replace_key: str | None = None,
        source_agents: list[str] | None = None,
    ) -> OverlayEvent:
        return OverlayEvent(
            id=f"ov-{ev.seq}-{otype.value}-{suffix}",
            match_id=self.meta.match_id,
            seq=ev.seq,
            clock=_clock(ev),
            type=otype,
            display=Display(
                slot=slot, duration_ms=duration_ms, priority=priority, anchor=anchor, replace_key=replace_key
            ),
            headline=headline,
            text=text,
            data=data or {},
            evidence=evidence or {},
            significance=round(max(0.0, min(1.0, significance)), 2),
            source_agents=source_agents or ["stats", "personalizer"],
            provider=provider,
            audience=self.profile.audience(),
        )

    def _involves(self, item: NarrativeItem) -> tuple[bool, bool]:
        fav = bool(self.profile.favorite_club) and item.team_id == self.profile.favorite_club
        focus = bool(self.profile.focus_player) and (
            item.player_id == self.profile.focus_player or self.profile.focus_player in item.involved_players
        )
        return fav, focus

    def _perspective(self) -> str:
        return self.profile.favorite_club or self.meta.home.id

    def _team_view(self, team_id: str, snap: dict[str, Any]) -> dict[str, Any]:
        t = self.meta.team(team_id)
        return {"id": t.id, "short": t.short, "color": t.color, "score": snap["score"][t.id]}

    # ----------------------------------------------------------------- render
    async def render(self, upd: StatsUpdate, items: list[NarrativeItem]) -> list[OverlayEvent]:
        ev = upd.event
        out: list[OverlayEvent] = []
        snap = self.stats.snapshot()

        is_goal = ev.type == EventType.SHOT and ev.outcome == "goal"
        if ev.type in (EventType.KICKOFF, EventType.HALF_TIME, EventType.FULL_TIME) or is_goal:
            status = {EventType.HALF_TIME: "half_time", EventType.FULL_TIME: "full_time"}.get(ev.type, "live")
            out.append(
                self._ov(
                    ev,
                    OverlayType.SCOREBUG,
                    "score",
                    Slot.TOP_LEFT,
                    duration_ms=0,
                    priority=95,
                    significance=0.9,
                    replace_key="scorebug",
                    data={
                        "home": self._team_view(self.meta.home.id, snap),
                        "away": self._team_view(self.meta.away.id, snap),
                        "status": status,
                        "period": ev.period,
                    },
                )
            )
        if is_goal and self.profile.favorite_club:
            scored = ev.team_id == self.profile.favorite_club
            mode = "analyst" if self.analyst else "casual"
            out.append(
                self._ov(
                    ev,
                    OverlayType.FAN_ALERT,
                    "fan",
                    Slot.FULLSCREEN,
                    duration_ms=4000,
                    priority=99,
                    significance=1.0,
                    text=i18n.render(
                        "fan_scored" if scored else "fan_conceded",
                        mode,
                        self.lang,
                        {"score": self.storyteller.score_line(upd.score), "xg": round(ev.xg or 0, 2)},
                    ),
                    data={"club": self.profile.favorite_club, "scored": scored},
                    source_agents=["stats", "personalizer"],
                )
            )
        if (
            ev.type == EventType.SHOT
            and ev.x is not None
            and (self.analyst or is_goal or (ev.xg or 0) >= 0.2)
        ):
            out.append(
                self._ov(
                    ev,
                    OverlayType.EVENT_MARKER,
                    "shot",
                    Slot.PITCH_ANCHOR,
                    duration_ms=5000,
                    priority=50,
                    significance=0.5,
                    anchor=_anchor((ev.x, ev.y)),
                    data={
                        "kind": "shot",
                        "outcome": ev.outcome,
                        "xg": ev.xg,
                        "team_id": ev.team_id,
                        "end": {"x": ev.end_x, "y": ev.end_y},
                        "speed_kmh": ev.speed_kmh,
                    },
                )
            )
        if self.analyst and ev.type == EventType.TACKLE and ev.x is not None:
            out.append(
                self._ov(
                    ev,
                    OverlayType.EVENT_MARKER,
                    "tackle",
                    Slot.PITCH_ANCHOR,
                    duration_ms=3000,
                    priority=30,
                    significance=0.3,
                    anchor=_anchor((ev.x, ev.y)),
                    data={"kind": "tackle", "team_id": ev.team_id},
                )
            )

        if ev.type == EventType.TRACKING:
            out.extend(self._periodic(ev, snap))

        for item in items:
            ov = await self._narrative(ev, item)
            if ov is not None:
                out.append(ov)

        if (
            self.profile.focus_player
            and ev.type != EventType.TRACKING
            and any(self._involves(i)[1] for i in items)
        ):
            out.append(self._player_card(ev))
        return out

    def _periodic(self, ev: MatchEvent, snap: dict[str, Any]) -> list[OverlayEvent]:
        out: list[OverlayEvent] = []
        minute = ev.mark_minute
        persp = self._perspective()
        m = snap["momentum"] if persp == self.meta.home.id else -snap["momentum"]
        if self.analyst or minute % 5 == 0:
            if abs(m) < 15:
                top_text = i18n.label("balanced", self.lang)
            else:
                lead = persp if m > 0 else self.meta.opponent(persp).id
                top_text = f"{i18n.label('who_is_on_top', self.lang)}: {self.meta.team(lead).short}"
            data: dict[str, Any] = {
                "momentum": round(m, 1),
                "perspective_team": persp,
                "scale": "-100..100, positive favours perspective_team",
            }
            if self.analyst:
                data["series"] = [
                    {"minute": k, "value": round(v if persp == self.meta.home.id else -v, 1)}
                    for k, v in sorted(self.stats.momentum_by_minute.items())[-15:]
                ]
            out.append(
                self._ov(
                    ev,
                    OverlayType.MOMENTUM,
                    "momentum",
                    Slot.TOP_RIGHT,
                    duration_ms=0,
                    priority=60,
                    significance=0.4,
                    replace_key="momentum",
                    data=data,
                    text=None if self.analyst else top_text,
                )
            )
        if (self.analyst and minute % 5 == 0) or (not self.analyst and minute % 15 == 0):
            keys = (
                [
                    "possession_pct",
                    "pass_accuracy_pct",
                    "avg_pass_difficulty",
                    "xg",
                    "shots",
                    "shots_on_target",
                    "pressures",
                    "tackles",
                    "final_third_entries",
                    "avg_ball_speed_kmh",
                    "top_shot_speed_kmh",
                ]
                if self.analyst
                else ["possession_pct", "shots"]
            )
            label_keys = {
                "possession_pct": "possession",
                "pass_accuracy_pct": "pass_accuracy",
                "avg_pass_difficulty": "pass_difficulty",
                "xg": "xg",
                "shots": "shots",
                "pressures": "pressures",
                "top_shot_speed_kmh": "top_shot_speed",
            }
            teams = {tid: {k: snap["teams"][tid][k] for k in keys} for tid in snap["teams"]}
            labels = {k: i18n.label(label_keys[k], self.lang) for k in keys if k in label_keys}
            if self.analyst:
                dist = {}
                for tid in snap["teams"]:
                    team = self.meta.team(tid)
                    dist[tid] = round(
                        sum(self.stats.players[p.id].distance_m for p in team.players) / 1000, 1
                    )
                for tid in teams:
                    teams[tid]["distance_km"] = dist[tid]
                labels["distance_km"] = i18n.label("distance_km", self.lang)
            out.append(
                self._ov(
                    ev,
                    OverlayType.STAT_PANEL,
                    "panel",
                    Slot.SIDE_PANEL,
                    duration_ms=0,
                    priority=40,
                    significance=0.4,
                    replace_key="stat_panel",
                    data={"teams": teams, "labels": labels, "minute": ev.minute + 1},
                )
            )
        if self.profile.focus_player and minute % 5 == 0:
            out.append(self._player_card(ev))
        return out

    def _player_card(self, ev: MatchEvent) -> OverlayEvent:
        pid = self.profile.focus_player or ""
        player = self.meta.find_player(pid)
        ps = self.stats.player_snapshot(pid)
        keys = (
            list(ps)
            if self.analyst
            else ["passes", "pass_accuracy_pct", "shots", "goals", "distance_km", "top_speed_kmh"]
        )
        return self._ov(
            ev,
            OverlayType.PLAYER_CARD,
            "card",
            Slot.SIDE_PANEL,
            duration_ms=0,
            priority=70,
            significance=0.6,
            replace_key="player_card",
            headline=f"{i18n.label('your_player', self.lang)}: {player.name if player else pid}",
            data={
                "player_id": pid,
                "number": player.number if player else None,
                "position": player.position if player else None,
                "team_id": player.team_id if player else None,
                "stats": {k: ps[k] for k in keys},
            },
        )

    async def _narrative(self, ev: MatchEvent, item: NarrativeItem) -> OverlayEvent | None:
        fav, focus = self._involves(item)
        sig = item.significance + (FAV_BOOST if fav else 0) + (FOCUS_BOOST if focus else 0)
        threshold = ANALYST_THRESHOLD if self.analyst else CASUAL_THRESHOLD
        if self.profile.focus_player and not focus:
            threshold += FOCUS_PENALTY
        always = item.key in ("half_time", "full_time", "kickoff") or item.key == "goal"
        if not always and sig < threshold:
            return None
        sig = min(1.0, sig)
        voiced = await self.storyteller.voice(item, self.profile, significance=sig)
        otype = {
            "commentary": OverlayType.COMMENTARY,
            "insight": OverlayType.INSIGHT,
            "milestone": OverlayType.MILESTONE,
            "player_tag": OverlayType.PLAYER_TAG,
        }[item.overlay_type]
        if otype == OverlayType.INSIGHT:
            slot = Slot.LOWER_THIRD
            duration = 12000 if self.analyst else 8000
        elif otype == OverlayType.PLAYER_TAG:
            slot = Slot.PITCH_ANCHOR
            duration = 5000
        elif sig >= 0.85:
            slot = Slot.LOWER_THIRD
            duration = 7000
        else:
            slot = Slot.TICKER
            duration = 6000
        evidence = item.evidence
        if not self.analyst and evidence:
            keep = [
                k
                for k in (
                    "xg",
                    "shot_speed_kmh",
                    "pass_difficulty",
                    "speed",
                    "km",
                    "momentum_now",
                    "possession_share_pct",
                )
                if k in evidence
            ][:2]
            evidence = {k: evidence[k] for k in keep}
        data: dict[str, Any] = {
            "key": item.key,
            "team_id": item.team_id,
            "player_id": item.player_id,
            "favorite_club": fav,
            "focus_player": focus,
        }
        if self.analyst and item.reasons:
            data["reasons"] = [c for c, _ in item.reasons]
        anchor = (
            _anchor(item.anchor) if otype == OverlayType.PLAYER_TAG or slot == Slot.PITCH_ANCHOR else None
        )
        if otype == OverlayType.PLAYER_TAG and anchor is None:
            slot = Slot.TICKER
        return self._ov(
            ev,
            otype,
            item.id.removeprefix("nar-"),
            slot,
            duration_ms=duration,
            priority=int(round(sig * 100)),
            significance=sig,
            provider=voiced.result.provider,
            headline=voiced.headline,
            text=voiced.text,
            data=data,
            evidence=evidence,
            anchor=anchor,
            source_agents=[*item.source_agents, "personalizer"],
        )

    async def render_recap(self, ev: MatchEvent, facts: dict[str, Any]) -> OverlayEvent:
        recap = await self.storyteller.voice_recap(facts, self.profile)
        data = {
            "score": facts["score"],
            "scorers": facts["scorers"],
            "player_of_the_match": facts["player_of_the_match"],
            "key_moments": facts["key_moments"],
            "title": i18n.label("recap", self.lang),
        }
        if self.analyst:
            data["stats"] = facts["stats"]
            data["xg"] = facts["xg"]
            data["possession"] = facts["possession"]
        return self._ov(
            ev,
            OverlayType.RECAP,
            "recap",
            Slot.FULLSCREEN,
            duration_ms=0,
            priority=100,
            significance=1.0,
            provider=recap["provider"],
            headline=recap["headline"],
            text=recap["text"],
            data=data,
            source_agents=["stats", "explainer", "storyteller", "personalizer"],
        )
