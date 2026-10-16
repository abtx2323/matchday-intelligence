"""FastAPI app: REST + Server-Sent Events API and the static broadcast UI."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi import Path as PathParam
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .agents.profile import ViewerProfile
from .config import Settings
from .limits import RateLimiter, StreamSlots, client_ip
from .orchestrator import MatchSession
from .providers.factory import build_primary, build_shared_state
from .schema import Language, Mode, StreamEnvelope, overlay_json_schema
from .simulator import MatchSimulator

log = logging.getLogger("matchday")
STATIC_DIR = Path(__file__).parent / "static"
MIN_LIVE_SPEED = 5.0

Seed = Annotated[int, Query(ge=0, le=1_000_000, description="Match seed (deterministic)")]
SeedPath = Annotated[int, PathParam(ge=0, le=1_000_000, description="Match seed")]
ModeQ = Annotated[Mode, Query(description="analyst | casual")]
LangQ = Annotated[Language, Query(description="en | es | fr")]
ClubQ = Annotated[str | None, Query(pattern=r"^[A-Z]{3}$", description="Favorite club id")]
PlayerQ = Annotated[str | None, Query(pattern=r"^[A-Z]{3}-\d{1,2}$", description="Focus player id")]
SpeedQ = Annotated[
    float | None,
    Query(ge=0, le=600, allow_inf_nan=False, description="Match seconds per real second: 0 (max) or 5-600"),
]
StartQ = Annotated[float, Query(ge=0, le=7200, allow_inf_nan=False, description="Resume from match second")]
FaultQ = Annotated[
    float, Query(ge=0, le=0.5, allow_inf_nan=False, description="Injected provider failure rate")
]


def _profile(meta: Any, mode: Mode, lang: Language, club: str | None, player: str | None) -> ViewerProfile:
    if club and club not in (meta.home.id, meta.away.id):
        raise HTTPException(422, "favorite_club is not playing in this match")
    if player and not meta.find_player(player):
        raise HTTPException(422, "focus_player is not in this match")
    return ViewerProfile(mode=mode, language=lang, favorite_club=club, focus_player=player)


# ---------------------------------------------------------------------------------------------
# Deterministic, cached batch computations. They use the offline template voice (no model spend),
# run in a worker thread (never on the event loop), and are memoised per seed + profile.
def _run_batch(seed: int, profiles: tuple[ViewerProfile, ...]) -> tuple[MatchSession, dict]:
    session = MatchSession(seed, list(profiles))
    return session, asyncio.run(session.run_all())


def _timeline_data(seed: int, profile: ViewerProfile) -> dict[str, Any]:
    session, tl = _run_batch(seed, (profile,))
    return {
        "meta": session.meta.to_dict(),
        "audience": profile.audience().model_dump(),
        "count": len(tl[profile.key]),
        "overlays": [o.model_dump(mode="json") for o in tl[profile.key]],
        "trace": {"handoffs": session.trace.handoffs},
    }


# Results are cached as compact JSON bytes (far smaller than Python object graphs).
@lru_cache(maxsize=32)
def _timeline(seed: int, profile: ViewerProfile) -> bytes:
    return json.dumps(_timeline_data(seed, profile), separators=(",", ":")).encode()


@lru_cache(maxsize=32)
def _compare(seed: int, analyst: ViewerProfile, casual: ViewerProfile, limit: int) -> bytes:
    session, tl = _run_batch(seed, (analyst, casual))

    def pick(key: str) -> list[dict[str, Any]]:
        items = [o for o in tl[key] if o.type in ("insight", "recap")]
        items.sort(key=lambda o: (-o.significance, o.clock.t))
        return [o.model_dump(mode="json") for o in items[:limit]]

    body = {
        "meta": session.meta.to_dict(),
        "analyst": {"total_overlays": len(tl[analyst.key]), "top": pick(analyst.key)},
        "casual": {"total_overlays": len(tl[casual.key]), "top": pick(casual.key)},
    }
    return json.dumps(body, separators=(",", ":")).encode()


def _recap(seed: int, profile: ViewerProfile) -> bytes | None:
    recaps = [o for o in json.loads(_timeline(seed, profile))["overlays"] if o["type"] == "recap"]
    return json.dumps(recaps[-1], separators=(",", ":")).encode() if recaps else None


def _json(body: bytes) -> Response:
    return Response(content=body, media_type="application/json")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    state: dict[str, Any] = {
        "primary": None,
        "shared": build_shared_state(settings),
        "ready": False,
        "slots": StreamSlots(settings.max_concurrent_streams, settings.max_streams_per_ip),
        "heavy": asyncio.Semaphore(settings.max_heavy_requests),
        "limiter": RateLimiter(settings.rate_limit_per_minute, 60.0),
    }

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Fail fast: a misconfigured cloud provider must stop startup, not degrade silently.
        state["primary"] = build_primary(settings)
        state["ready"] = True
        log.info("provider=%s", settings.provider)
        try:
            yield
        finally:
            state["ready"] = False
            primary = state["primary"]
            if primary is not None and hasattr(primary, "aclose"):
                await primary.aclose()

    app = FastAPI(
        title="Matchday Intelligence",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            ip = client_ip(request, settings.trusted_proxy_hops)
            if not state["limiter"].allow(ip):
                return _secure(
                    request,
                    JSONResponse(
                        {"detail": "rate limit exceeded"}, status_code=429, headers={"Retry-After": "60"}
                    ),
                )
        return _secure(request, await call_next(request))

    def _secure(request: Request, response: Response) -> Response:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if not request.url.path.startswith("/api/docs"):
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
            )
        return response

    if settings.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.allowed_origins),
            allow_methods=["GET"],
            allow_headers=["Accept", "Content-Type"],
            allow_credentials=False,
        )

    async def heavy(fn, *args):
        sem: asyncio.Semaphore = state["heavy"]
        try:
            await asyncio.wait_for(sem.acquire(), timeout=10)
        except TimeoutError:
            raise HTTPException(503, "server busy, try again shortly") from None
        try:
            return await run_in_threadpool(fn, *args)
        finally:
            sem.release()

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        ok = state["ready"] and (settings.provider == "template" or state["primary"] is not None)
        body = {
            "status": "ok" if ok else "unavailable",
            "version": __version__,
            "provider": settings.provider,
            "circuit_open": state["shared"].breaker.opened_at is not None,
        }
        return JSONResponse(body, status_code=200 if ok else 503)

    @app.get("/api/schema")
    async def schema() -> dict[str, Any]:
        return overlay_json_schema()

    @app.get("/api/match/{seed}")
    async def match_meta(seed: SeedPath) -> dict[str, Any]:
        return MatchSimulator(seed).meta.to_dict()

    @app.get("/api/match/{seed}/events")
    async def match_events(seed: SeedPath, limit: Annotated[int, Query(ge=1, le=5000)] = 5000) -> Response:
        sim = MatchSimulator(seed)
        evs = sim.events()[:limit]
        return {"meta": sim.meta.to_dict(), "count": len(evs), "events": [e.to_dict() for e in evs]}

    @app.get("/api/match/{seed}/timeline")
    async def timeline(
        seed: SeedPath,
        mode: ModeQ = Mode.CASUAL,
        lang: LangQ = Language.EN,
        club: ClubQ = None,
        player: PlayerQ = None,
    ) -> Response:
        prof = _profile(MatchSimulator(seed).meta, mode, lang, club, player)
        return _json(await heavy(_timeline, seed, prof))

    @app.get("/api/match/{seed}/compare")
    async def compare(
        seed: SeedPath,
        lang: LangQ = Language.EN,
        club: ClubQ = None,
        player: PlayerQ = None,
        limit: Annotated[int, Query(ge=1, le=20)] = 6,
    ) -> Response:
        """Same match, same intelligence, two audiences: proves the experiences differ."""
        meta = MatchSimulator(seed).meta
        analyst = _profile(meta, Mode.ANALYST, lang, club, player)
        casual = _profile(meta, Mode.CASUAL, lang, club, player)
        return _json(await heavy(_compare, seed, analyst, casual, limit))

    @app.get("/api/match/{seed}/recap")
    async def recap(
        seed: SeedPath,
        mode: ModeQ = Mode.CASUAL,
        lang: LangQ = Language.EN,
        club: ClubQ = None,
        player: PlayerQ = None,
    ) -> dict[str, Any]:
        prof = _profile(MatchSimulator(seed).meta, mode, lang, club, player)
        result = await heavy(_recap, seed, prof)
        if result is None:
            raise HTTPException(404, "recap not available for this match")
        return _json(result)

    @app.get("/api/stream")
    async def stream(
        request: Request,
        seed: Seed = 6,
        mode: ModeQ = Mode.CASUAL,
        lang: LangQ = Language.EN,
        club: ClubQ = None,
        player: PlayerQ = None,
        speed: SpeedQ = None,
        start: StartQ = 0.0,
        faults: FaultQ = 0.0,
    ) -> StreamingResponse:
        live_speed = settings.default_speed if speed is None else speed
        if 0 < live_speed < MIN_LIVE_SPEED:
            raise HTTPException(422, "speed must be 0 or between 5 and 600")
        meta = MatchSimulator(seed).meta
        prof = _profile(meta, mode, lang, club, player)
        ip = client_ip(request, settings.trusted_proxy_hops)
        slots: StreamSlots = state["slots"]
        if not slots.available(ip):
            raise HTTPException(503, "too many concurrent streams, try again shortly")

        async def gen():
            # The slot is taken inside the generator so it is always released by ``finally``.
            if not slots.try_acquire(ip):
                yield 'event: end\ndata: {"kind": "end", "reason": "busy", "trace": {}}\n\n'
                return
            try:
                s = MatchSession(
                    seed,
                    [prof],
                    settings,
                    primary=state["primary"],
                    shared=state["shared"],
                    fault_rate=faults,
                    client_key=ip,
                )
                hello = {
                    "kind": "meta",
                    "meta": meta.to_dict(),
                    "audience": prof.audience().model_dump(),
                    "provider": s.voice.primary_name,
                    "speed": live_speed,
                    "start": start,
                }
                yield f"event: meta\ndata: {json.dumps(hello)}\n\n"
                loop = asyncio.get_running_loop()
                deadline = loop.time() + settings.max_stream_seconds
                reason = "full_time"
                async for ev, per in s.run(speed=live_speed, start_t=start):
                    if await request.is_disconnected():
                        reason = "disconnected"
                        break
                    if loop.time() > deadline:
                        reason = "max_duration"
                        break
                    if ev.t < start and ev.type != "full_time":
                        continue
                    env = StreamEnvelope(kind="overlays", t=ev.t, event=ev.to_dict(), overlays=per[prof.key])
                    yield f"event: overlays\ndata: {env.model_dump_json()}\n\n"
                end = {"kind": "end", "reason": reason, "trace": s.trace.summary()}
                yield f"event: end\ndata: {json.dumps(end)}\n\n"
            finally:
                slots.release(ip)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.exception_handler(ValueError)
    async def value_error(_request: Request, exc: ValueError) -> JSONResponse:
        log.info("rejected request: %r", exc)
        return JSONResponse({"detail": "invalid request"}, status_code=422)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
