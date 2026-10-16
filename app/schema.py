"""Overlay event schema: the machine-readable contract between the pipeline and any renderer.

Every item the pipeline emits is an ``OverlayEvent``. Renderers (our web UI, a broadcast
graphics engine, a streaming client) only need this schema. ``GET /api/schema`` serves the
JSON Schema and ``docs/overlay-schema.json`` is the committed copy.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"


class Mode(StrEnum):
    ANALYST = "analyst"
    CASUAL = "casual"


class Language(StrEnum):
    EN = "en"
    ES = "es"
    FR = "fr"


class OverlayType(StrEnum):
    SCOREBUG = "scorebug"
    STAT_PANEL = "stat_panel"
    MOMENTUM = "momentum"
    PLAYER_TAG = "player_tag"
    PLAYER_CARD = "player_card"
    COMMENTARY = "commentary"
    INSIGHT = "insight"
    MILESTONE = "milestone"
    FAN_ALERT = "fan_alert"
    EVENT_MARKER = "event_marker"
    RECAP = "recap"
    AGENT_TRACE = "agent_trace"


class Slot(StrEnum):
    """Where a renderer should place the overlay."""

    TOP_LEFT = "top_left"
    TOP_RIGHT = "top_right"
    LOWER_THIRD = "lower_third"
    SIDE_PANEL = "side_panel"
    TICKER = "ticker"
    PITCH_ANCHOR = "pitch_anchor"
    FULLSCREEN = "fullscreen"
    HIDDEN = "hidden"


class Anchor(BaseModel):
    """Pitch coordinates in metres (0..105 x 0..68) for pitch-anchored overlays."""

    x: float = Field(ge=0, le=105)
    y: float = Field(ge=0, le=68)


class Display(BaseModel):
    slot: Slot
    duration_ms: int = Field(ge=0, le=600_000, description="0 means persistent until replaced")
    priority: int = Field(ge=0, le=100, description="Higher wins when slots collide")
    anchor: Anchor | None = None
    replace_key: str | None = Field(
        default=None, description="Overlays with the same key replace each other (e.g. scorebug)"
    )


class Audience(BaseModel):
    mode: Mode
    language: Language
    favorite_club: str | None = None
    focus_player: str | None = None


class MatchClock(BaseModel):
    t: float = Field(ge=0, description="Match seconds since kickoff, continuous across halves")
    period: int = Field(ge=1, le=2)
    display: str = Field(description="mm:ss match clock")


class OverlayEvent(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    schema_version: str = SCHEMA_VERSION
    id: str = Field(description="Stable id: same seed + profile => same ids")
    match_id: str
    seq: int = Field(ge=0, description="Source match event sequence number that triggered it")
    clock: MatchClock
    type: OverlayType
    display: Display
    headline: str | None = Field(default=None, max_length=200)
    text: str | None = Field(default=None, max_length=1200)
    data: dict[str, Any] = Field(default_factory=dict, description="Type-specific payload")
    evidence: dict[str, Any] = Field(
        default_factory=dict, description="Stats that justify the claim (explainability)"
    )
    significance: float = Field(ge=0, le=1)
    source_agents: list[str] = Field(description="Agents that contributed, in handoff order")
    provider: str = Field(description="Language provider that voiced the text")
    audience: Audience


class StreamEnvelope(BaseModel):
    """One SSE message: the source event plus the overlays it produced for this viewer."""

    kind: str = Field(description="'overlays', 'meta', or 'end'")
    t: float
    event: dict[str, Any] | None = None
    overlays: list[OverlayEvent] = Field(default_factory=list)


def overlay_json_schema() -> dict[str, Any]:
    schema = OverlayEvent.model_json_schema()
    schema["$id"] = "https://github.com/abtx2323/matchday-intelligence/docs/overlay-schema.json"
    schema["title"] = "Matchday Intelligence OverlayEvent"
    return schema
