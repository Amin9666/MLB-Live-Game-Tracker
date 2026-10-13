import httpx
import pytest
import respx

from tracker.mlb_client import MlbClient, RetryableStatus

BASE = "https://statsapi.mlb.com"


@respx.mock
async def test_retries_server_errors_then_succeeds() -> None:
    route = respx.get(f"{BASE}/api/v1.1/game/1/feed/live").mock(
        side_effect=[httpx.Response(503), httpx.Response(502), httpx.Response(200, json={"ok": 1})]
    )
    async with MlbClient(BASE, backoff_base=0) as client:
        assert await client.live_feed(1) == {"ok": 1}
    assert route.call_count == 3


@respx.mock
async def test_honors_retry_after_on_429() -> None:
    route = respx.get(f"{BASE}/api/v1.1/game/1/feed/live").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json={"ok": 1}),
        ]
    )
    async with MlbClient(BASE, backoff_base=0) as client:
        assert await client.live_feed(1) == {"ok": 1}
    assert route.call_count == 2


@respx.mock
async def test_retries_transport_errors() -> None:
    respx.get(f"{BASE}/api/v1.1/game/1/feed/live").mock(
        side_effect=[httpx.ConnectTimeout("slow"), httpx.Response(200, json={"ok": 1})]
    )
    async with MlbClient(BASE, backoff_base=0) as client:
        assert await client.live_feed(1) == {"ok": 1}


@respx.mock
async def test_gives_up_after_max_attempts() -> None:
    route = respx.get(f"{BASE}/api/v1.1/game/1/feed/live").mock(return_value=httpx.Response(500))
    async with MlbClient(BASE, backoff_base=0, max_attempts=3) as client:
        with pytest.raises(RetryableStatus):
            await client.live_feed(1)
    assert route.call_count == 3


@respx.mock
async def test_does_not_retry_client_errors() -> None:
    route = respx.get(f"{BASE}/api/v1.1/game/1/feed/live").mock(return_value=httpx.Response(404))
    async with MlbClient(BASE, backoff_base=0) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await client.live_feed(1)
    assert route.call_count == 1


@respx.mock
async def test_timecode_is_passed_through() -> None:
    route = respx.get(
        f"{BASE}/api/v1.1/game/1/feed/live", params={"timecode": "20261012_003000"}
    ).mock(return_value=httpx.Response(200, json={}))
    async with MlbClient(BASE) as client:
        await client.live_feed(1, "20261012_003000")
    assert route.called
