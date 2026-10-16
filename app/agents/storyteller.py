"""Storyteller agent: turns events, stat signals and insights into narrative items, and voices
them (commentary lines, milestones, full-time recap) in the viewer's language and style.

Narrative items are language-neutral. ``voice`` is the handoff point where the Personalizer
asks the Storyteller for text tailored to one viewer; it goes through the resilient
``VoiceService`` (cloud model with template fallback).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import PITCH_LENGTH, EventType, MatchMeta
from ..providers import VoiceRequest, VoiceResult, VoiceService
from . import i18n
from .explainer import Insight
from .profile import ViewerProfile
from .stats import StatsAgent, StatsUpdate

INSIGHT_KEYS = {
    "goal": "goal",
    "big_chance": "big_chance",
    "elite_pass": "elite_pass",
    "momentum_swing": "momentum_swing",
    "rhythm_control": "rhythm_control",
    "rhythm_chaos": "rhythm_chaos",
    "pressure_shift": "pressure_shift",
}


@dataclass
class NarrativeItem:
    id: str
    key: str
    overlay_type: str  # commentary | insight | milestone | player_tag
    t: float
    seq: int
    period: int
    team_id: str | None
    player_id: str | None
    significance: float
    params: dict[str, Any]
    reasons: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    anchor: tuple[float, float] | None = None
    source_agents: list[str] = field(default_factory=list)
    involved_players: tuple[str, ...] = ()


@dataclass
class Voiced:
    headline: str | None
    text: str
    result: VoiceResult


class StorytellerAgent:
    name = "storyteller"

    def __init__(self, meta: MatchMeta, stats: StatsAgent, voice: VoiceService) -> None:
        self.meta = meta
        self.stats = stats
        self.voice_service = voice
        self.items: list[NarrativeItem] = []

    # ----------------------------------------------------------------- naming
    def _pname(self, pid: str | None) -> str:
        p = self.meta.find_player(pid) if pid else None
        return p.name if p else "?"

    def _tname(self, tid: str | None) -> str:
        if not tid:
            return "?"
        return self.meta.team(tid).short

    def score_line(self, score: dict[str, int]) -> str:
        h, a = self.meta.home, self.meta.away
        return f"{h.short} {score.get(h.id, 0)}-{score.get(a.id, 0)} {a.short}"

    def _zone_key(self, team_id: str, x: float) -> str:
        fx = x if team_id == self.meta.home.id else PITCH_LENGTH - x
        if fx < 35:
            return "defensive_third"
        if fx < 70:
            return "middle_third"
        return "attacking_third"

    def _base(self, upd: StatsUpdate, team_id: str | None, player_id: str | None) -> dict[str, Any]:
        ev = upd.event
        return {
            "minute": ev.minute + 1,
            "team": self._tname(team_id),
            "player": self._pname(player_id),
            "score": self.score_line(upd.score),
            "home": self.meta.home.short,
            "away": self.meta.away.short,
        }

    def _team_lines(self) -> dict[str, str]:
        snap = self.stats.snapshot()["teams"]
        h, a = snap[self.meta.home.id], snap[self.meta.away.id]
        return {
            "poss": f"{h['possession_pct']:.0f}%-{a['possession_pct']:.0f}%",
            "xg_line": f"{h['xg']:.2f}-{a['xg']:.2f}",
            "acc_line": f"{h['pass_accuracy_pct']:.0f}%-{a['pass_accuracy_pct']:.0f}%",
        }

    # ----------------------------------------------------------------- narrate
    def narrate(self, upd: StatsUpdate, insights: list[Insight]) -> list[NarrativeItem]:
        ev = upd.event
        items: list[NarrativeItem] = []
        covered_seq = {i.seq for i in insights if i.kind in ("goal", "big_chance")}

        def add(
            key: str,
            otype: str,
            sig: float,
            team_id: str | None,
            player_id: str | None,
            params: dict[str, Any],
            suffix: str = "",
            **kw: Any,
        ) -> None:
            items.append(
                NarrativeItem(
                    id=f"nar-{ev.seq}-{key}{suffix}",
                    key=key,
                    overlay_type=otype,
                    t=ev.t,
                    seq=ev.seq,
                    period=ev.period,
                    team_id=team_id,
                    player_id=player_id,
                    significance=round(min(1.0, sig), 2),
                    params={**self._base(upd, team_id, player_id), **params},
                    **kw,
                )
            )

        if ev.type == EventType.KICKOFF and ev.extra.get("period_start"):
            add(
                "kickoff",
                "commentary",
                0.6,
                ev.team_id,
                None,
                {
                    "home_style": self.meta.home.style,
                    "away_style": self.meta.away.style,
                },
                source_agents=["simulator", "storyteller"],
            )
        elif ev.type == EventType.SHOT and ev.seq not in covered_seq and ev.outcome != "goal":
            key = {"saved": "shot_saved", "blocked": "shot_blocked"}.get(ev.outcome or "", "shot_off_target")
            add(
                key,
                "commentary",
                0.35 + (ev.xg or 0) * 0.8,
                ev.team_id,
                ev.player_id,
                {
                    "xg": round(ev.xg or 0, 2),
                    "dist": ev.distance_m,
                    "speed": ev.speed_kmh,
                },
                evidence={"xg": ev.xg, "shot_speed_kmh": ev.speed_kmh, "distance_m": ev.distance_m},
                anchor=(ev.x or 0, ev.y or 0),
                source_agents=["stats", "storyteller"],
                involved_players=(ev.player_id or "",),
            )
        elif ev.type == EventType.TACKLE and ev.team_id:
            zone = self._zone_key(ev.team_id, ev.x or 0)
            add(
                "tackle",
                "commentary",
                0.3 + (0.15 if zone == "defensive_third" else 0),
                ev.team_id,
                ev.player_id,
                {"zone_key": zone},
                anchor=(ev.x or 0, ev.y or 0),
                source_agents=["stats", "storyteller"],
                involved_players=(ev.player_id or "", ev.opponent_id or ""),
            )
        elif ev.type in (EventType.HALF_TIME, EventType.FULL_TIME):
            key = "half_time" if ev.type == EventType.HALF_TIME else "full_time"
            add(
                key,
                "commentary",
                0.9,
                None,
                None,
                self._team_lines(),
                evidence=self.stats.snapshot(),
                source_agents=["stats", "storyteller"],
            )

        for s in upd.signals:
            if s.kind == "elite_pass":
                continue  # surfaced through the Explainer's insight instead
            otype = "player_tag" if s.kind.startswith("tag_") else "milestone"
            add(
                s.kind,
                otype,
                s.significance,
                s.team_id,
                s.player_id,
                dict(s.params),
                suffix=f"-{s.player_id or s.team_id}",
                evidence=dict(s.params),
                anchor=(ev.x, ev.y) if ev.x is not None else None,
                source_agents=["stats", "storyteller"],
                involved_players=(s.player_id,) if s.player_id else (),
            )

        for ins in insights:
            params = dict(ins.params)
            if ins.kind == "elite_pass":
                params["receiver"] = self._pname(ins.params.get("receiver_id"))
            if ins.kind in ("goal",):
                params.update(
                    {"dist": ins.evidence.get("distance_m"), "speed": ins.evidence.get("shot_speed_kmh")}
                )
            items.append(
                NarrativeItem(
                    id=f"nar-{ins.id}",
                    key=INSIGHT_KEYS[ins.kind],
                    overlay_type="insight",
                    t=ins.t,
                    seq=ins.seq,
                    period=ev.period,
                    team_id=ins.team_id,
                    player_id=ins.player_id,
                    significance=ins.significance,
                    params={**self._base(upd, ins.team_id, ins.player_id), **params},
                    reasons=ins.reasons,
                    evidence=ins.evidence,
                    anchor=ins.anchor,
                    source_agents=["stats", "explainer", "storyteller"],
                    involved_players=tuple(p for p in (ins.player_id, ins.params.get("receiver_id")) if p),
                )
            )
        self.items.extend(items)
        return items

    # ----------------------------------------------------------------- voice
    def _localize(self, params: dict[str, Any], lang: str) -> dict[str, Any]:
        out = dict(params)
        if "zone_key" in out:
            out["zone"] = i18n.label(out["zone_key"], lang)
        for k in ("home_style", "away_style"):
            if k in out:
                out[k] = i18n.style_name(out[k], lang)
        return out

    async def voice(
        self, item: NarrativeItem, profile: ViewerProfile, significance: float | None = None
    ) -> Voiced:
        lang, mode = str(profile.language), str(profile.mode)
        params = self._localize(item.params, lang)
        sig = item.significance if significance is None else significance
        if item.overlay_type == "insight":
            headline = i18n.render(item.key, mode, lang, params)
            reasons = item.reasons if mode == "analyst" else item.reasons[:1]
            fallback = " ".join(i18n.why(code, lang, {**params, **rp}) for code, rp in reasons)
            facts = {
                "headline": headline,
                "reasons": [c for c, _ in item.reasons],
                "evidence": item.evidence if mode == "analyst" else {},
                **params,
            }
            req = VoiceRequest(
                task="insight",
                key=item.key,
                facts=facts,
                language=lang,
                mode=mode,
                fallback_text=fallback,
                significance=sig,
                max_words=45 if mode == "analyst" else 22,
                cache_key=f"{item.id}|{lang}|{mode}",
            )
            res = await self.voice_service.voice(req)
            return Voiced(headline, res.text, res)
        fallback = i18n.render(item.key, mode, lang, params)
        req = VoiceRequest(
            task="commentary",
            key=item.key,
            facts=params,
            language=lang,
            mode=mode,
            fallback_text=fallback,
            significance=sig,
            max_words=30 if mode == "analyst" else 16,
            cache_key=f"{item.id}|{lang}|{mode}",
        )
        res = await self.voice_service.voice(req)
        return Voiced(None, res.text, res)

    # ----------------------------------------------------------------- recap
    def recap_facts(self, insights: list[Insight]) -> dict[str, Any]:
        snap = self.stats.snapshot()
        h, a = self.meta.home, self.meta.away
        score = snap["score"]
        teams = snap["teams"]
        scorers = [
            f"{self._pname(g['player_id'])} {g['minute']}' ({self._tname(g['team_id'])})"
            for g in self.stats.goals
        ]
        key_moments = sorted(insights, key=lambda i: (-i.significance, i.t))[:3]
        potm_id, potm_rating = self.stats.top_players(1)[0]
        return {
            "score": score,
            "score_line": self.score_line(score),
            "home": h.short,
            "away": a.short,
            "home_id": h.id,
            "away_id": a.id,
            "scorers": scorers,
            "xg_line": f"{teams[h.id]['xg']:.2f}-{teams[a.id]['xg']:.2f}",
            "xg": {h.id: teams[h.id]["xg"], a.id: teams[a.id]["xg"]},
            "possession": {h.id: teams[h.id]["possession_pct"], a.id: teams[a.id]["possession_pct"]},
            "key_moments": [
                {
                    "id": i.id,
                    "kind": i.kind,
                    "minute": i.params.get("minute", int(i.t // 60) + 1),
                    "team_id": i.team_id,
                    "player": self._pname(i.player_id),
                    "significance": i.significance,
                }
                for i in key_moments
            ],
            "player_of_the_match": {
                "player_id": potm_id,
                "name": self._pname(potm_id),
                "team": self._tname(self.meta.find_player(potm_id).team_id),  # type: ignore[union-attr]
                "rating": potm_rating,
            },
            "stats": snap,
        }

    def _moment_label(self, m: dict[str, Any], lang: str) -> str:
        names = {
            "goal": {"en": "goal", "es": "gol", "fr": "but"},
            "big_chance": {"en": "big chance", "es": "ocasión clara", "fr": "grosse occasion"},
            "elite_pass": {"en": "elite pass", "es": "pase de élite", "fr": "passe d'élite"},
            "momentum_swing": {
                "en": "momentum swing",
                "es": "cambio de inercia",
                "fr": "bascule du momentum",
            },
            "rhythm_control": {"en": "control phase", "es": "fase de control", "fr": "phase de contrôle"},
            "rhythm_chaos": {"en": "chaos phase", "es": "fase de caos", "fr": "phase de chaos"},
            "pressure_shift": {
                "en": "pressing shift",
                "es": "cambio de presión",
                "fr": "changement de pressing",
            },
        }
        label = names.get(m["kind"], {}).get(lang, m["kind"])
        who = m["player"] if m["player"] != "?" else (self._tname(m["team_id"]) if m["team_id"] else "")
        return f"{m['minute']}' {label}" + (f" ({who})" if who else "")

    async def voice_recap(self, facts: dict[str, Any], profile: ViewerProfile) -> dict[str, Any]:
        lang, mode = str(profile.language), str(profile.mode)
        h_id, a_id = facts["home_id"], facts["away_id"]
        sh, sa = facts["score"][h_id], facts["score"][a_id]
        if sh == sa:
            headline = i18n.recap(
                "headline_draw", lang, {"home": facts["home"], "away": facts["away"], "score": f"{sh}-{sa}"}
            )
        else:
            win, lose = (facts["home"], facts["away"]) if sh > sa else (facts["away"], facts["home"])
            headline = i18n.recap(
                "headline_win", lang, {"winner": win, "loser": lose, "score": f"{max(sh, sa)}-{min(sh, sa)}"}
            )
        parts: list[str] = []
        parts.append(
            i18n.recap("scorers", lang, {"list": ", ".join(facts["scorers"])})
            if facts["scorers"]
            else i18n.recap("no_goals", lang, {})
        )
        xg_h, xg_a = facts["xg"][h_id], facts["xg"][a_id]
        xg_winner = h_id if xg_h > xg_a else a_id
        real_winner = h_id if sh > sa else (a_id if sa > sh else None)
        if abs(xg_h - xg_a) >= 0.6 and real_winner != xg_winner:
            parts.append(
                i18n.recap(
                    "xg_story_unfair", lang, {"xg_line": facts["xg_line"], "team": self._tname(xg_winner)}
                )
            )
        else:
            parts.append(i18n.recap("xg_story_fair", lang, {"xg_line": facts["xg_line"]}))
        poss_team = h_id if facts["possession"][h_id] >= facts["possession"][a_id] else a_id
        if mode == "analyst":
            parts.append(
                i18n.recap(
                    "possession",
                    lang,
                    {"team": self._tname(poss_team), "pct": round(facts["possession"][poss_team])},
                )
            )
            if facts["key_moments"]:
                parts.append(
                    i18n.recap(
                        "key_moments",
                        lang,
                        {"list": "; ".join(self._moment_label(m, lang) for m in facts["key_moments"])},
                    )
                )
        potm = facts["player_of_the_match"]
        parts.append(
            i18n.recap("potm", lang, {"player": potm["name"], "team": potm["team"], "rating": potm["rating"]})
        )
        if profile.focus_player and self.meta.find_player(profile.focus_player):
            ps = self.stats.player_snapshot(profile.focus_player)
            parts.append(
                i18n.recap(
                    "focus",
                    lang,
                    {
                        "player": self._pname(profile.focus_player),
                        "passes": ps["passes"],
                        "acc": round(ps["pass_accuracy_pct"]),
                        "shots": ps["shots"],
                        "km": ps["distance_km"],
                        "top": ps["top_speed_kmh"],
                    },
                )
            )
        if profile.favorite_club in (h_id, a_id):
            fav = profile.favorite_club
            opp = a_id if fav == h_id else h_id
            fs, os_ = facts["score"][fav], facts["score"][opp]
            parts.append(
                i18n.recap("fan_win" if fs > os_ else "fan_loss" if fs < os_ else "fan_draw", lang, {})
            )
        fallback = " ".join(parts)
        req = VoiceRequest(
            task="recap",
            key="recap",
            facts={k: v for k, v in facts.items() if k != "stats"} | {"headline": headline},
            language=lang,
            mode=mode,
            fallback_text=fallback,
            significance=1.0,
            max_words=110 if mode == "analyst" else 60,
            cache_key=f"recap|{self.meta.match_id}|{profile.key}",
        )
        res = await self.voice_service.voice(req)
        return {
            "headline": headline,
            "text": res.text,
            "provider": res.provider,
            "fallback_used": res.fallback_used,
            "facts": facts,
        }
