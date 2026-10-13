import asyncio
import contextlib
import logging
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from tracker.stream import latest_id, parse_id, range_after, read_after, stream_key, to_message

log = logging.getLogger(__name__)


class Subscription:
    def __init__(self, game_pk: int, maxsize: int = 2000) -> None:
        self.game_pk = game_pk
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize)
        self.overflowed = asyncio.Event()

    def offer(self, msg: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(msg)
        except asyncio.QueueFull:
            self.overflowed.set()


class GameHub:
    def __init__(self, redis: Redis, block_ms: int = 15_000) -> None:
        self.redis = redis
        self.block_ms = block_ms
        self._subs: dict[int, set[Subscription]] = {}
        self._readers: dict[int, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, game_pk: int) -> Subscription:
        sub = Subscription(game_pk)
        self._subs.setdefault(game_pk, set()).add(sub)
        async with self._lock:
            if game_pk not in self._readers:
                start = await self._latest_id(game_pk)
                self._readers[game_pk] = asyncio.create_task(self._read(game_pk, start))
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        subs = self._subs.get(sub.game_pk)
        if subs is None:
            return
        subs.discard(sub)
        if not subs:
            self._subs.pop(sub.game_pk, None)
            task = self._readers.pop(sub.game_pk, None)
            if task:
                task.cancel()

    async def history(self, game_pk: int, after: str) -> list[dict[str, Any]]:
        entries = await range_after(self.redis, stream_key(game_pk), after)
        return [to_message(eid, fields) for eid, fields in entries]

    async def latest_id(self, game_pk: int) -> str:
        return await self._latest_id(game_pk)

    async def _latest_id(self, game_pk: int) -> str:
        return await latest_id(self.redis, stream_key(game_pk))

    async def _read(self, game_pk: int, last: str) -> None:
        key = stream_key(game_pk)
        while True:
            try:
                entries = await read_after(self.redis, key, last, self.block_ms)
            except RedisError:
                log.exception("stream read failed for game %s; retrying", game_pk)
                await asyncio.sleep(1.0)
                continue
            for eid, fields in entries:
                last = eid
                msg = to_message(eid, fields)
                for sub in list(self._subs.get(game_pk, ())):
                    sub.offer(msg)

    async def close(self) -> None:
        for task in self._readers.values():
            task.cancel()
        for task in self._readers.values():
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._readers.clear()
        self._subs.clear()


def newer(msg_id: str, than: str) -> bool:
    return parse_id(msg_id) > parse_id(than)
