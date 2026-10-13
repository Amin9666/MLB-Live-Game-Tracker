import json
from typing import Any, cast

from redis.asyncio import Redis

LIVE_GAMES_KEY = "games:live"


def stream_key(game_pk: int) -> str:
    return f"game:{game_pk}:events"


def state_key(game_pk: int) -> str:
    return f"game:{game_pk}:state"


def parse_id(stream_id: str) -> tuple[int, int]:
    ms, _, seq = stream_id.partition("-")
    return int(ms), int(seq or 0)


StreamEntry = tuple[str, dict[str, str]]


async def read_after(
    redis: Redis, key: str, last: str, block_ms: int, count: int = 200
) -> list[StreamEntry]:
    resp = cast(Any, await redis.xread({key: last}, block=block_ms, count=count))
    return [(str(eid), dict(fields)) for _, entries in resp or [] for eid, fields in entries]


async def range_after(redis: Redis, key: str, after: str) -> list[StreamEntry]:
    start = f"({after}" if after not in ("0", "0-0") else "-"
    entries = cast(Any, await redis.xrange(key, min=start, max="+"))
    return [(str(eid), dict(fields)) for eid, fields in entries]


async def latest_id(redis: Redis, key: str) -> str:
    entries = cast(Any, await redis.xrevrange(key, count=1))
    return str(entries[0][0]) if entries else "0-0"


def blocking_client(url: str, block_ms: int) -> Redis:
    client: Redis = Redis.from_url(
        url,
        decode_responses=True,
        socket_timeout=block_ms / 1000 + 10,
        health_check_interval=30,
    )
    return client


def to_message(stream_id: str, fields: dict[str, str]) -> dict[str, Any]:
    return {"id": stream_id, "type": fields["type"], "data": json.loads(fields["data"])}


class EventBus:
    def __init__(self, redis: Redis, maxlen: int = 20_000) -> None:
        self.redis = redis
        self.maxlen = maxlen

    async def publish_many(
        self, game_pk: int, events: list[tuple[str, dict[str, Any]]]
    ) -> list[str]:
        if not events:
            return []
        async with self.redis.pipeline(transaction=False) as pipe:
            for event_type, payload in events:
                pipe.xadd(
                    stream_key(game_pk),
                    {"type": event_type, "data": json.dumps(payload, default=str)},
                    maxlen=self.maxlen,
                    approximate=True,
                )
            ids: list[str] = await pipe.execute()
        return ids

    async def set_state(self, game_pk: int, state: dict[str, Any], live: bool) -> None:
        async with self.redis.pipeline(transaction=False) as pipe:
            pipe.set(state_key(game_pk), json.dumps(state, default=str))
            if live:
                pipe.sadd(LIVE_GAMES_KEY, game_pk)
            else:
                pipe.srem(LIVE_GAMES_KEY, game_pk)
            await pipe.execute()

    async def reset(self, game_pk: int) -> None:
        await self.redis.delete(stream_key(game_pk), state_key(game_pk))
        await self.redis.srem(LIVE_GAMES_KEY, game_pk)
