import asyncio
import json
from collections.abc import Iterator
from typing import Any

import pytest
import redis as sync_redis
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.websockets import WebSocketDisconnect

from tests.conftest import GAME_PK, TEST_DATABASE_URL, TEST_REDIS_URL, ListFeedSource
from tracker.api import create_app
from tracker.config import Settings
from tracker.db import metadata
from tracker.ingest import GameIngester
from tracker.models.runtime import Models
from tracker.parse import parse_feed
from tracker.stream import EventBus, stream_key


async def _ingest(feeds: list[dict[str, Any]]) -> None:
    engine = create_async_engine(TEST_DATABASE_URL)
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    await redis.flushdb()
    async with engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
        await conn.run_sync(metadata.create_all)
    ingester = GameIngester(GAME_PK, ListFeedSource(feeds), engine, EventBus(redis), Models())
    while not ingester.finished:
        await ingester.step()
    await redis.aclose()
    await engine.dispose()


@pytest.fixture(scope="module")
def client(recorded: list[tuple[str, dict[str, Any]]]) -> Iterator[TestClient]:
    asyncio.run(_ingest([f for _, f in recorded[::5]] + [recorded[-1][1]]))
    settings = Settings(database_url=TEST_DATABASE_URL, redis_url=TEST_REDIS_URL)
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture(scope="module")
def n_pitches(final_frame: dict[str, Any]) -> int:
    return len(parse_feed(final_frame).pitches)


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"ok": True}


def test_games_by_date(client: TestClient) -> None:
    games = client.get("/api/games", params={"date": "2026-10-11"}).json()
    assert [g["game_pk"] for g in games] == [GAME_PK]
    assert games[0]["is_live"] is False
    assert client.get("/api/games", params={"date": "2026-10-01"}).json() == []
    assert client.get("/api/games/latest-date").json() == {"date": "2026-10-11"}


def test_snapshot(client: TestClient, n_pitches: int) -> None:
    snap = client.get(f"/api/games/{GAME_PK}/snapshot").json()
    assert snap["game"]["status"] == "Final"
    assert len(snap["pitches"]) == n_pitches
    assert len(snap["plate_appearances"]) == 70
    assert snap["state"]["home_score"] == 2
    assert snap["stream_id"] != "0-0"


def test_unknown_game_404(client: TestClient) -> None:
    assert client.get("/api/games/1/snapshot").status_code == 404


def test_winprob_series(client: TestClient, n_pitches: int) -> None:
    series = client.get(f"/api/games/{GAME_PK}/winprob").json()
    assert len(series) == n_pitches
    assert series[0]["inning"] == 1


def test_player_pitch_filters(client: TestClient) -> None:
    pitches = client.get(f"/api/games/{GAME_PK}/pitches").json()
    pitcher = pitches[0]["pitcher_id"]
    two_strike = client.get(
        f"/api/players/{pitcher}/pitches", params={"strikes": 2, "season": 2026}
    ).json()
    expected = [p for p in pitches if p["pitcher_id"] == pitcher and p["strikes"] == 2]
    assert len(two_strike) == len(expected) > 0
    assert client.get(f"/api/players/{pitcher}/pitches", params={"strikes": 3}).status_code == 422


def test_ws_replays_history_from_last_id(client: TestClient) -> None:
    r = sync_redis.Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    ids = [eid for eid, _ in r.xrange(stream_key(GAME_PK))]
    start = ids[-4]
    with client.websocket_connect(f"/ws/games/{GAME_PK}?last_id={start}") as ws:
        got = [ws.receive_json()["id"] for _ in range(3)]
    assert got == ids[-3:]


def test_ws_pushes_new_events(client: TestClient) -> None:
    r = sync_redis.Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    latest = client.get(f"/api/games/{GAME_PK}/snapshot").json()["stream_id"]
    with client.websocket_connect(f"/ws/games/{GAME_PK}?last_id={latest}") as ws:
        payload = {"note": "live"}
        new_id = r.xadd(stream_key(GAME_PK), {"type": "state", "data": json.dumps(payload)})
        msg = ws.receive_json()
    assert msg == {"id": new_id, "type": "state", "data": payload}


def test_ws_dollar_starts_from_now(client: TestClient) -> None:
    r = sync_redis.Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    with client.websocket_connect(f"/ws/games/{GAME_PK}?last_id=$") as ws:
        new_id = r.xadd(stream_key(GAME_PK), {"type": "state", "data": "{}"})
        assert ws.receive_json()["id"] == new_id


def test_ws_rejects_malformed_last_id(client: TestClient) -> None:
    with (
        client.websocket_connect(f"/ws/games/{GAME_PK}?last_id=abc") as ws,
        pytest.raises(WebSocketDisconnect) as exc,
    ):
        ws.receive_json()
    assert exc.value.code == 1008
