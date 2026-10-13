import asyncio

from redis.asyncio import Redis

from tests.conftest import TEST_REDIS_URL
from tracker.hub import GameHub
from tracker.stream import blocking_client, stream_key


async def test_reader_survives_socket_timeouts_longer_than_block(redis: Redis) -> None:
    short_timeout: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True, socket_timeout=0.3)
    hub = GameHub(short_timeout, block_ms=1000)
    try:
        sub = await hub.subscribe(1)
        await asyncio.sleep(1.5)
        new_id = await redis.xadd(stream_key(1), {"type": "state", "data": "{}"})
        msg = await asyncio.wait_for(sub.queue.get(), 5)
        assert msg["id"] == new_id
    finally:
        await hub.close()
        await short_timeout.aclose()


async def test_blocking_client_outlasts_quiet_periods(redis: Redis) -> None:
    client = blocking_client(TEST_REDIS_URL, block_ms=1500)
    hub = GameHub(client, block_ms=1500)
    try:
        sub = await hub.subscribe(2)
        await asyncio.sleep(3.5)
        assert not hub._readers[2].done()
        new_id = await redis.xadd(stream_key(2), {"type": "state", "data": "{}"})
        msg = await asyncio.wait_for(sub.queue.get(), 3)
        assert msg["id"] == new_id
    finally:
        await hub.close()
        await client.aclose()


async def test_late_subscriber_receives_only_new_events(redis: Redis) -> None:
    await redis.xadd(stream_key(3), {"type": "state", "data": "{}"})
    client = blocking_client(TEST_REDIS_URL, block_ms=500)
    hub = GameHub(client, block_ms=500)
    try:
        sub = await hub.subscribe(3)
        new_id = await redis.xadd(stream_key(3), {"type": "pitch", "data": "{}"})
        msg = await asyncio.wait_for(sub.queue.get(), 3)
        assert msg["id"] == new_id
        assert sub.queue.empty()
    finally:
        await hub.close()
        await client.aclose()
