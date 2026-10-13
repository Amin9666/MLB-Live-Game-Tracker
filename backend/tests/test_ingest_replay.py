import copy
import json
from collections import Counter
from typing import Any

import httpx
import respx
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import GAME_PK, ListFeedSource
from tracker import db, frames
from tracker.ingest import GameIngester, HttpFeedSource
from tracker.mlb_client import MlbClient
from tracker.models.runtime import Models
from tracker.parse import parse_feed
from tracker.stream import EventBus, stream_key

BASE = "https://statsapi.mlb.com"
Frames = list[tuple[str, dict[str, Any]]]


async def _drain(ingester: GameIngester) -> None:
    while not ingester.finished:
        await ingester.step()


async def _events(redis: Redis) -> list[tuple[str, dict[str, Any]]]:
    entries = await redis.xrange(stream_key(GAME_PK))
    return [(f["type"], json.loads(f["data"])) for _, f in entries]


def _ingester(
    source: Any, engine: AsyncEngine, redis: Redis, final_grace_polls: int = 10
) -> GameIngester:
    return GameIngester(
        GAME_PK,
        source,
        engine,
        EventBus(redis),
        Models(),
        full_frame_every=50,
        final_grace_polls=final_grace_polls,
    )


@respx.mock
async def test_replay_over_http_emits_each_pitch_once(
    recorded: Frames, final_frame: dict[str, Any], engine: AsyncEngine, redis: Redis
) -> None:
    respx.get(f"{BASE}/api/v1.1/game/{GAME_PK}/feed/live").mock(
        side_effect=[httpx.Response(200, json=f) for _, f in recorded]
    )
    async with MlbClient(BASE) as client:
        await _drain(_ingester(HttpFeedSource(client, floor=0), engine, redis, final_grace_polls=0))

    expected = parse_feed(final_frame)
    events = await _events(redis)
    kinds = Counter(kind for kind, _ in events)
    assert kinds["pitch"] == len(expected.pitches)
    assert kinds["final"] == 1
    pitch_keys = [(d["at_bat_index"], d["pitch_number"]) for k, d in events if k == "pitch"]
    assert len(set(pitch_keys)) == len(pitch_keys)
    assert {d["at_bat_index"] for k, d in events if k == "pa"} == {
        pa.at_bat_index for pa in expected.plate_appearances
    }

    async with engine.connect() as conn:
        n_pitches = await conn.scalar(select(func.count()).select_from(db.pitches))
        game = (await conn.execute(select(db.games))).one()
    assert n_pitches == len(expected.pitches)
    assert (game.status, game.away_score, game.home_score) == ("Final", 1, 2)


async def test_late_hit_data_is_emitted_as_correction(
    recorded: Frames, engine: AsyncEngine, redis: Redis
) -> None:
    await _drain(_ingester(ListFeedSource([f for _, f in recorded]), engine, redis))
    corrections = [d for k, d in await _events(redis) if k == "correction"]
    assert corrections
    assert all(c["hit_x"] is not None for c in corrections)


async def test_rerun_is_idempotent(recorded: Frames, engine: AsyncEngine, redis: Redis) -> None:
    feeds = [f for _, f in recorded]
    await _drain(_ingester(ListFeedSource(feeds), engine, redis))
    before = Counter(k for k, _ in await _events(redis))

    await _drain(_ingester(ListFeedSource(feeds), engine, redis))
    after = Counter(k for k, _ in await _events(redis))
    for kind in ("pitch", "correction", "pa"):
        assert after[kind] == before[kind]


async def test_restart_mid_game_does_not_duplicate(
    recorded: Frames, final_frame: dict[str, Any], engine: AsyncEngine, redis: Redis
) -> None:
    feeds = [f for _, f in recorded]
    half = len(feeds) // 2
    first = _ingester(ListFeedSource(feeds[:half]), engine, redis)
    for _ in range(half):
        await first.step()
    await _drain(_ingester(ListFeedSource(feeds[half:]), engine, redis))

    kinds = Counter(k for k, _ in await _events(redis))
    assert kinds["pitch"] == len(parse_feed(final_frame).pitches)


async def test_scoring_change_updates_row_and_emits_correction(
    final_frame: dict[str, Any], engine: AsyncEngine, redis: Redis
) -> None:
    changed = copy.deepcopy(final_frame)
    play = changed["liveData"]["plays"]["allPlays"][10]
    pitch_ev = next(e for e in play["playEvents"] if e.get("isPitch"))
    pitch_ev["details"]["type"] = {"code": "SI", "description": "Sinker"}
    changed["metaData"]["timeStamp"] = "20261012_040000"

    await _drain(_ingester(ListFeedSource([final_frame, changed]), engine, redis))

    corrections = [d for k, d in await _events(redis) if k == "correction"]
    assert len(corrections) == 1
    assert corrections[0]["pitch_type"] == "SI"
    async with engine.connect() as conn:
        stored = await conn.scalar(
            select(db.pitches.c.pitch_type).where(
                db.pitches.c.at_bat_index == play["about"]["atBatIndex"],
                db.pitches.c.pitch_number == pitch_ev["pitchNumber"],
            )
        )
    assert stored == "SI"


async def test_raw_events_rebuild_every_frame(
    recorded: Frames, engine: AsyncEngine, redis: Redis
) -> None:
    await _drain(_ingester(ListFeedSource([f for _, f in recorded]), engine, redis))
    async with engine.connect() as conn:
        rows = await db.raw_frames(conn, GAME_PK)
    kinds = Counter(kind for _, kind, _ in rows)
    assert kinds["full"] >= 2
    rebuilt = [f for _, f in frames.rebuild(rows)]
    distinct = [f for i, (_, f) in enumerate(recorded) if i == 0 or f != recorded[i - 1][1]]
    assert rebuilt == distinct


async def test_degraded_feed_does_not_erase_tracking_data(
    final_frame: dict[str, Any], engine: AsyncEngine, redis: Redis
) -> None:
    degraded = copy.deepcopy(final_frame)
    for play in degraded["liveData"]["plays"]["allPlays"]:
        for ev in play["playEvents"]:
            ev.pop("pitchData", None)
            ev.pop("hitData", None)
            ev.pop("playId", None)
            ev.get("details", {}).pop("type", None)
    degraded["metaData"]["timeStamp"] = "20261012_040000"
    restored = copy.deepcopy(final_frame)
    restored["metaData"]["timeStamp"] = "20261012_040010"

    ingester = _ingester(ListFeedSource([final_frame, degraded, restored]), engine, redis)
    results = []
    while not ingester.finished:
        result, _ = await ingester.step()
        if result:
            results.append(result)

    assert results[1].regressions_ignored == len(parse_feed(final_frame).pitches)
    assert not any(k == "correction" for k, _ in await _events(redis))
    async with engine.connect() as conn:
        missing = await conn.scalar(
            select(func.count()).select_from(db.pitches).where(db.pitches.c.start_speed.is_(None))
        )
    assert missing == 0


async def test_future_stamped_frame_does_not_block_later_frames(
    final_frame: dict[str, Any], engine: AsyncEngine, redis: Redis
) -> None:
    future = copy.deepcopy(final_frame)
    future["metaData"]["timeStamp"] = "20991231_000000"
    changed = copy.deepcopy(final_frame)
    changed["metaData"]["timeStamp"] = "20261012_040000"
    pitch_ev = next(
        e for e in changed["liveData"]["plays"]["allPlays"][5]["playEvents"] if e.get("isPitch")
    )
    pitch_ev["details"]["call"] = {"code": "S", "description": "Swinging Strike"}

    ingester = _ingester(ListFeedSource([future, changed]), engine, redis)
    await _drain(ingester)

    assert ingester.stale_frames == 0
    assert [d["call_code"] for k, d in await _events(redis) if k == "correction"] == ["S"]


@respx.mock
async def test_http_source_bypasses_stale_cache(final_frame: dict[str, Any]) -> None:
    live = copy.deepcopy(final_frame)
    live["gameData"]["status"]["abstractGameState"] = "Live"
    newer = copy.deepcopy(live)
    newer["metaData"]["timeStamp"] = "20261012_031300"
    stuck_tc = live["metaData"]["timeStamp"]
    respx.get(
        f"{BASE}/api/v1.1/game/{GAME_PK}/feed/live", params={"timecode": "20261012_031300"}
    ).mock(return_value=httpx.Response(200, json=newer))
    respx.get(f"{BASE}/api/v1.1/game/{GAME_PK}/feed/live").mock(
        return_value=httpx.Response(200, json=live)
    )
    respx.get(f"{BASE}/api/v1.1/game/{GAME_PK}/feed/live/timestamps").mock(
        return_value=httpx.Response(200, json=[stuck_tc, "20261012_031300"])
    )
    now = [0.0]
    async with MlbClient(BASE) as client:
        source = HttpFeedSource(client, floor=0, stale_after=45, clock=lambda: now[0])
        assert frames.timecode_of(await source.fetch(GAME_PK) or {}) == stuck_tc
        now[0] = 30
        assert frames.timecode_of(await source.fetch(GAME_PK) or {}) == stuck_tc
        now[0] = 50
        assert frames.timecode_of(await source.fetch(GAME_PK) or {}) == "20261012_031300"
    assert source.cache_bypasses == 1


async def test_finished_game_stream_expires(
    final_frame: dict[str, Any], engine: AsyncEngine, redis: Redis
) -> None:
    bus = EventBus(redis, finished_ttl=3600)
    ingester = GameIngester(
        GAME_PK, ListFeedSource([final_frame]), engine, bus, Models(), final_grace_polls=0
    )
    await _drain(ingester)
    ttl = await redis.ttl(stream_key(GAME_PK))
    assert 0 < ttl <= 3600
