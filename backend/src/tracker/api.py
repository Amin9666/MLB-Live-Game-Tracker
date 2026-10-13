import asyncio
import contextlib
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Header, HTTPException, Query, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis
from sqlalchemy import Select, func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from starlette.websockets import WebSocketDisconnect, WebSocketState

from tracker.config import Settings, get_settings
from tracker.db import games, pitches, plate_appearances
from tracker.hub import GameHub, newer
from tracker.stream import (
    LIVE_GAMES_KEY,
    blocking_client,
    read_after,
    state_key,
    stream_key,
    to_message,
)

ET = ZoneInfo("America/New_York")
STREAM_ID = re.compile(r"\d+(-\d+)?")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
        app.state.stream_redis = blocking_client(settings.redis_url, settings.stream_block_ms)
        app.state.hub = GameHub(app.state.stream_redis, block_ms=settings.stream_block_ms)
        yield
        await app.state.hub.close()
        await app.state.stream_redis.aclose()
        await app.state.redis.aclose()
        await app.state.engine.dispose()

    app = FastAPI(title="MLB Live Tracker", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    def engine(request: Request) -> AsyncEngine:
        e: AsyncEngine = request.app.state.engine
        return e

    def redis(request: Request) -> Redis:
        r: Redis = request.app.state.redis
        return r

    async def fetch_all(request: Request, stmt: Select[Any]) -> list[dict[str, Any]]:
        async with engine(request).connect() as conn:
            result = await conn.execute(stmt)
            return [dict(r._mapping) for r in result]

    @app.get("/healthz")
    async def healthz(request: Request) -> dict[str, bool]:
        async with engine(request).connect() as conn:
            await conn.execute(text("select 1"))
        await redis(request).ping()
        return {"ok": True}

    @app.get("/api/games")
    async def list_games(
        request: Request, day: Annotated[date | None, Query(alias="date")] = None
    ) -> Any:
        day = day or datetime.now(ET).date()
        rows = await fetch_all(
            request, select(games).where(games.c.game_date == day).order_by(games.c.game_pk)
        )
        live = {int(pk) for pk in await redis(request).smembers(LIVE_GAMES_KEY)}
        for row in rows:
            row["is_live"] = row["game_pk"] in live
        return rows

    @app.get("/api/games/latest-date")
    async def latest_date(request: Request) -> dict[str, date | None]:
        async with engine(request).connect() as conn:
            value = await conn.scalar(select(func.max(games.c.game_date)))
        return {"date": value}

    @app.get("/api/games/{game_pk}/snapshot")
    async def snapshot(request: Request, game_pk: int) -> Any:
        stream_id = await request.app.state.hub.latest_id(game_pk)
        game_rows = await fetch_all(request, select(games).where(games.c.game_pk == game_pk))
        if not game_rows:
            raise HTTPException(404, "game not found")
        raw_state = await redis(request).get(state_key(game_pk))
        return {
            "stream_id": stream_id,
            "game": game_rows[0],
            "state": json.loads(raw_state) if raw_state else None,
            "pitches": await fetch_all(request, _game_pitches(game_pk)),
            "plate_appearances": await fetch_all(
                request,
                select(plate_appearances)
                .where(plate_appearances.c.game_pk == game_pk)
                .order_by(plate_appearances.c.at_bat_index),
            ),
        }

    @app.get("/api/games/{game_pk}/pitches")
    async def game_pitches(request: Request, game_pk: int) -> Any:
        return await fetch_all(request, _game_pitches(game_pk))

    @app.get("/api/games/{game_pk}/winprob")
    async def win_prob(request: Request, game_pk: int) -> Any:
        stmt = (
            select(
                pitches.c.at_bat_index,
                pitches.c.pitch_number,
                pitches.c.inning,
                pitches.c.half,
                pitches.c.home_wp,
            )
            .where(pitches.c.game_pk == game_pk)
            .order_by(pitches.c.at_bat_index, pitches.c.pitch_number)
        )
        return await fetch_all(request, stmt)

    @app.get("/api/players/{player_id}/pitches")
    async def player_pitches(
        request: Request,
        player_id: int,
        role: Literal["pitcher", "batter"] = "pitcher",
        season: int | None = None,
        balls: Annotated[int | None, Query(ge=0, le=3)] = None,
        strikes: Annotated[int | None, Query(ge=0, le=2)] = None,
        pitch_type: str | None = None,
        limit: Annotated[int, Query(ge=1, le=5000)] = 500,
    ) -> Any:
        col = pitches.c.pitcher_id if role == "pitcher" else pitches.c.batter_id
        stmt = select(pitches).where(col == player_id)
        if season is not None:
            stmt = stmt.join(games, games.c.game_pk == pitches.c.game_pk).where(
                games.c.season == season
            )
        if balls is not None:
            stmt = stmt.where(pitches.c.balls == balls)
        if strikes is not None:
            stmt = stmt.where(pitches.c.strikes == strikes)
        if pitch_type:
            stmt = stmt.where(pitches.c.pitch_type == pitch_type.upper())
        stmt = stmt.order_by(pitches.c.start_time.desc()).limit(limit)
        return await fetch_all(request, stmt)

    @app.websocket("/ws/games/{game_pk}")
    async def ws_game(ws: WebSocket, game_pk: int, last_id: str = "0-0") -> None:
        await ws.accept()
        if last_id != "$" and not STREAM_ID.fullmatch(last_id):
            await ws.close(code=1008, reason="last_id must be a stream id or $")
            return
        hub: GameHub = ws.app.state.hub
        sub = await hub.subscribe(game_pk)
        if last_id == "$":
            last_id = await hub.latest_id(game_pk)

        async def pump() -> None:
            sent = last_id
            for msg in await hub.history(game_pk, last_id):
                await ws.send_json(msg)
                sent = msg["id"]
            overflow = asyncio.ensure_future(sub.overflowed.wait())
            try:
                while True:
                    getter = asyncio.ensure_future(sub.queue.get())
                    try:
                        done, _ = await asyncio.wait(
                            {getter, overflow}, return_when=asyncio.FIRST_COMPLETED
                        )
                    finally:
                        getter.cancel()
                    if overflow in done:
                        await ws.close(code=1013, reason="client too slow; reconnect with last_id")
                        return
                    msg = getter.result()
                    if newer(msg["id"], sent):
                        await ws.send_json(msg)
                        sent = msg["id"]
            finally:
                overflow.cancel()

        task = asyncio.create_task(pump())
        try:
            while not task.done():
                receiver = asyncio.ensure_future(ws.receive())
                try:
                    done, _ = await asyncio.wait(
                        {receiver, task}, return_when=asyncio.FIRST_COMPLETED
                    )
                finally:
                    if not receiver.done():
                        receiver.cancel()
                if receiver in done and receiver.result()["type"] == "websocket.disconnect":
                    break
        except WebSocketDisconnect:
            pass
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await task
            hub.unsubscribe(sub)
            if ws.client_state == WebSocketState.CONNECTED:
                with contextlib.suppress(RuntimeError):
                    await ws.close()

    @app.get("/sse/games/{game_pk}")
    async def sse_game(
        request: Request,
        game_pk: int,
        last_event_id: Annotated[str | None, Header()] = None,
        last_id: str | None = None,
    ) -> StreamingResponse:
        r: Redis = request.app.state.stream_redis
        start = last_event_id or last_id or "0-0"

        async def events() -> AsyncIterator[str]:
            cursor = start
            while not await request.is_disconnected():
                entries = await read_after(r, stream_key(game_pk), cursor, settings.stream_block_ms)
                if not entries:
                    yield ": keepalive\n\n"
                    continue
                for eid, fields in entries:
                    cursor = eid
                    msg = to_message(eid, fields)
                    yield f"id: {eid}\nevent: {msg['type']}\ndata: {json.dumps(msg)}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    if settings.static_dir and settings.static_dir.exists():
        static_dir = settings.static_dir.resolve()
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> FileResponse:
            candidate = static_dir / path
            if path and candidate.is_file() and static_dir in candidate.resolve().parents:
                return FileResponse(candidate)
            return FileResponse(static_dir / "index.html")

    return app


def _game_pitches(game_pk: int) -> Select[Any]:
    return (
        select(pitches)
        .where(pitches.c.game_pk == game_pk)
        .order_by(pitches.c.at_bat_index, pitches.c.pitch_number)
    )


app = create_app()
