"""FastAPI app: REST + Server-Sent Events API and the static broadcast UI."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi import Path as PathParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .agents.profile import ViewerProfile
from .config import Settings
from .orchestrator import MatchSession
from .providers.factory import build_primary
from .schema import Language, Mode, StreamEnvelope, overlay_json_schema
from .simulator import MatchSimulator

log = logging.getLogger("matchday")
STATIC_DIR = Path(__file__).parent / "static"

Seed = Annotated[int, Query(ge=0, le=1_000_000, description="Match seed (deterministic)")]
SeedPath = Annotated[int, PathParam(ge=0, le=1_000_000, description="Match seed")]
ModeQ = Annotated[Mode, Query(description="analyst | casual")]
LangQ = Annotated[Language, Query(description="en | es | fr")]
ClubQ = Annotated[str | None, Query(pattern=r"^[A-Z]{3}$", description="Favorite club id")]
PlayerQ = Annotated[str | None, Query(pattern=r"^[A-Z]{3}-\d{1,2}$", description="Focus player id")]
SpeedQ = Annotated[float, Query(ge=0, le=600, description="Match seconds per real second; 0=max")]
StartQ = Annotated[float, Query(ge=0, le=7200, description="Resume from match second")]
FaultQ = Annotated[float, Query(ge=0, le=0.5, description="Injected provider failure rate")]


def _profile(meta: Any, mode: Mode, lang: Language, club: str | None, player: str | None) -> ViewerProfile:
    if club and club not in (meta.home.id, meta.away.id):
        raise HTTPException(422, "favorite_club is not playing in this match")
    if player and not meta.find_player(player):
        raise HTTPException(422, "focus_player is not in this match")
    return ViewerProfile(mode=mode, language=lang, favorite_club=club, focus_player=player)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    state: dict[str, Any] = {"primary": None, "streams": asyncio.Semaphore(settings.max_concurrent_streams)}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            state["primary"] = build_primary(settings)
        except Exception:  # noqa: BLE001 - never fail startup on optional cloud config
            log.exception("Could not configure cloud provider; using offline templates")
            state["primary"] = None
        log.info("provider=%s", state["primary"].name if state["primary"] else "template")
        yield

    app = FastAPI(
        title="Matchday Intelligence",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    if settings.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.allowed_origins),
            allow_methods=["GET"],
            allow_headers=["Accept", "Content-Type"],
            allow_credentials=False,
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
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

    def session(seed: int, profiles: list[ViewerProfile], fault_rate: float = 0.0) -> MatchSession:
        return MatchSession(seed, profiles, settings, primary=state["primary"], fault_rate=fault_rate)

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "provider": state["primary"].name if state["primary"] else "template",
        }

    @app.get("/api/schema")
    async def schema() -> dict[str, Any]:
        return overlay_json_schema()

    @app.get("/api/match/{seed}")
    async def match_meta(seed: SeedPath) -> dict[str, Any]:
        sim = MatchSimulator(seed)
        return sim.meta.to_dict()

    @app.get("/api/match/{seed}/events")
    async def match_events(
        seed: SeedPath, limit: Annotated[int, Query(ge=1, le=5000)] = 5000
    ) -> dict[str, Any]:
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
    ) -> dict[str, Any]:
        meta = MatchSimulator(seed).meta
        prof = _profile(meta, mode, lang, club, player)
        s = session(seed, [prof])
        tl = await s.run_all()
        return {
            "meta": meta.to_dict(),
            "audience": prof.audience().model_dump(),
            "count": len(tl[prof.key]),
            "overlays": [o.model_dump(mode="json") for o in tl[prof.key]],
            "trace": s.trace.summary(),
        }

    @app.get("/api/match/{seed}/compare")
    async def compare(
        seed: SeedPath,
        lang: LangQ = Language.EN,
        club: ClubQ = None,
        player: PlayerQ = None,
        limit: Annotated[int, Query(ge=1, le=20)] = 6,
    ) -> dict[str, Any]:
        """Same match, same intelligence, two audiences: proves the experiences differ."""
        meta = MatchSimulator(seed).meta
        analyst = _profile(meta, Mode.ANALYST, lang, club, player)
        casual = _profile(meta, Mode.CASUAL, lang, club, player)
        s = session(seed, [analyst, casual])
        tl = await s.run_all()
        keep = {"insight", "recap"}

        def pick(key: str) -> list[dict[str, Any]]:
            items = [o for o in tl[key] if o.type in keep]
            items.sort(key=lambda o: (-o.significance, o.clock.t))
            return [o.model_dump(mode="json") for o in items[:limit]]

        return {
            "meta": meta.to_dict(),
            "analyst": {"total_overlays": len(tl[analyst.key]), "top": pick(analyst.key)},
            "casual": {"total_overlays": len(tl[casual.key]), "top": pick(casual.key)},
        }

    @app.get("/api/match/{seed}/recap")
    async def recap(
        seed: SeedPath,
        mode: ModeQ = Mode.CASUAL,
        lang: LangQ = Language.EN,
        club: ClubQ = None,
        player: PlayerQ = None,
    ) -> dict[str, Any]:
        meta = MatchSimulator(seed).meta
        prof = _profile(meta, mode, lang, club, player)
        s = session(seed, [prof])
        tl = await s.run_all()
        recaps = [o for o in tl[prof.key] if o.type == "recap"]
        return recaps[-1].model_dump(mode="json")

    @app.get("/api/stream")
    async def stream(
        request: Request,
        seed: Seed = 6,
        mode: ModeQ = Mode.CASUAL,
        lang: LangQ = Language.EN,
        club: ClubQ = None,
        player: PlayerQ = None,
        speed: SpeedQ = settings.default_speed,
        start: StartQ = 0.0,
        faults: FaultQ = 0.0,
    ) -> StreamingResponse:
        meta = MatchSimulator(seed).meta
        prof = _profile(meta, mode, lang, club, player)
        sem: asyncio.Semaphore = state["streams"]
        if sem.locked():
            raise HTTPException(503, "too many concurrent streams, try again shortly")
        await sem.acquire()

        async def gen():
            try:
                s = session(seed, [prof], fault_rate=faults)
                hello = {
                    "kind": "meta",
                    "meta": meta.to_dict(),
                    "audience": prof.audience().model_dump(),
                    "provider": s.voice.primary_name,
                    "speed": speed,
                    "start": start,
                }
                yield f"event: meta\ndata: {json.dumps(hello)}\n\n"
                async for ev, per in s.run(speed=speed, start_t=start):
                    if await request.is_disconnected():
                        break
                    if ev.t < start and ev.type != "full_time":
                        continue
                    env = StreamEnvelope(kind="overlays", t=ev.t, event=ev.to_dict(), overlays=per[prof.key])
                    yield f"event: overlays\ndata: {env.model_dump_json()}\n\n"
                yield (f"event: end\ndata: {json.dumps({'kind': 'end', 'trace': s.trace.summary()})}\n\n")
            finally:
                sem.release()

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.exception_handler(ValueError)
    async def value_error(_request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)[:200]}, status_code=422)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
