import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tracker import frames
from tracker.db import metadata

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://tracker:tracker@localhost:5432/tracker_test"
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
FIXTURES = Path(__file__).parent / "fixtures"
GAME_PK = 849809
RECORDING = FIXTURES / f"{GAME_PK}.jsonl.gz"


class ListFeedSource:
    def __init__(self, feeds: list[dict[str, Any]]) -> None:
        self.feeds = list(feeds)

    async def fetch(self, game_pk: int) -> dict[str, Any] | None:
        return self.feeds.pop(0) if self.feeds else None

    def next_delay(self, wait_hint: float) -> float:
        return 0.0


@pytest.fixture(scope="session")
def recorded() -> list[tuple[str, dict[str, Any]]]:
    return list(frames.read_recording(RECORDING))


@pytest.fixture(scope="session")
def final_frame(recorded: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    return recorded[-1][1]


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(TEST_DATABASE_URL)
    async with eng.begin() as conn:
        await conn.run_sync(metadata.drop_all)
        await conn.run_sync(metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    r: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    await r.flushdb()
    yield r
    await r.aclose()
