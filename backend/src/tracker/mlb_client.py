import asyncio
import logging
import random
from datetime import date
from types import TracebackType
from typing import Any, Self

import httpx

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class RetryableStatus(Exception):
    def __init__(self, response: httpx.Response) -> None:
        super().__init__(f"{response.status_code} {response.request.url}")
        self.response = response


class MlbClient:
    def __init__(
        self,
        base_url: str = "https://statsapi.mlb.com",
        *,
        concurrency: int = 8,
        max_attempts: int = 5,
        backoff_base: float = 0.5,
        timeout: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            headers={"User-Agent": "mlb-live-tracker/0.1"},
        )
        self._sem = asyncio.Semaphore(concurrency)
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        for attempt in range(self.max_attempts):
            try:
                async with self._sem:
                    resp = await self._client.get(path, params=params)
                if resp.status_code in RETRYABLE_STATUS:
                    raise RetryableStatus(resp)
                resp.raise_for_status()
                return resp.json()
            except (httpx.TransportError, RetryableStatus) as exc:
                if attempt == self.max_attempts - 1:
                    raise
                delay = self._delay(attempt, exc)
                log.warning("GET %s failed (%s); retry %d in %.1fs", path, exc, attempt + 1, delay)
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    def _delay(self, attempt: int, exc: Exception) -> float:
        if isinstance(exc, RetryableStatus):
            retry_after = exc.response.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                return float(retry_after)
        return float(min(self.backoff_base * 2**attempt, 30.0) + random.random() * 0.25)

    async def schedule(
        self, start: date, end: date | None = None, game_types: str = "R,F,D,L,W"
    ) -> list[dict[str, Any]]:
        data = await self.get_json(
            "/api/v1/schedule",
            params={
                "sportId": 1,
                "startDate": start.isoformat(),
                "endDate": (end or start).isoformat(),
                "gameType": game_types,
            },
        )
        return [g for d in data.get("dates", []) for g in d.get("games", [])]

    async def live_feed(self, game_pk: int, timecode: str | None = None) -> dict[str, Any]:
        params = {"timecode": timecode} if timecode else None
        result: dict[str, Any] = await self.get_json(
            f"/api/v1.1/game/{game_pk}/feed/live", params=params
        )
        return result

    async def timestamps(self, game_pk: int) -> list[str]:
        result: list[str] = await self.get_json(f"/api/v1.1/game/{game_pk}/feed/live/timestamps")
        return result
