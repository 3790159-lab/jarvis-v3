import httpx
import pytest

from app.services.block_m_common.faceswap_client import PredictionFailed
from app.services.block_m_common.lucataco_client import LucatacoClient, LucatacoTransientError


def _client(handler):
    transport = httpx.MockTransport(handler)
    return LucatacoClient(api_token="t", transport=transport)


@pytest.mark.asyncio
async def test_succeeded_returns_output_url():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json={"id": "p1"})
        return httpx.Response(
            200, json={"status": "succeeded", "output": "https://x/y.jpg"}
        )

    out = await _client(handler).swap("data:image/jpeg;base64,A", "data:image/jpeg;base64,B")
    assert out == "https://x/y.jpg"


@pytest.mark.asyncio
async def test_succeeded_none_output_returns_none():
    # lucataco's no-face outcome: succeeded + output=None (billable, no retry).
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json={"id": "p2"})
        return httpx.Response(200, json={"status": "succeeded", "output": None})

    out = await _client(handler).swap("data:image/jpeg;base64,A", "data:image/jpeg;base64,B")
    assert out is None


@pytest.mark.asyncio
async def test_failed_raises_prediction_failed_no_retry():
    calls = {"post": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            calls["post"] += 1
            return httpx.Response(201, json={"id": "p3"})
        return httpx.Response(200, json={"status": "failed", "error": "boom"})

    with pytest.raises(PredictionFailed):
        await _client(handler).swap("data:image/jpeg;base64,A", "data:image/jpeg;base64,B")
    assert calls["post"] == 1  # never retried


@pytest.mark.asyncio
async def test_429_exhausted_raises_lucataco_transient_error():
    """All retry attempts return 429 → must raise LucatacoTransientError (not plain RuntimeError).

    LucatacoTransientError subclasses RuntimeError so existing pytest.raises(RuntimeError)
    still passes, but this test verifies the exact type for the sweep classification.
    """
    calls = {"post": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            calls["post"] += 1
            return httpx.Response(429, json={"detail": "rate limited"})
        # poll should never be reached
        return httpx.Response(200, json={"status": "succeeded", "output": "https://x/y.jpg"})

    # Use max_retries=2 via _run_prediction directly to keep the test fast.
    # Payload is production-shaped (as swap() builds it) so it passes the DEV-3
    # money-preflight guard and the 429 retry-exhaustion path is what's exercised;
    # the guard's own empty-payload rejection is covered in test_money_preflight_clients.py.
    client = _client(handler)
    with pytest.raises(LucatacoTransientError):
        await client._run_prediction(
            {
                "version": "v",
                "input": {
                    "swap_image": "data:image/jpeg;base64,A",
                    "target_image": "data:image/jpeg;base64,B",
                },
            },
            max_retries=2,
        )
    # Verifies it IS a RuntimeError subclass (backward compat for any existing catches).
    assert issubclass(LucatacoTransientError, RuntimeError)
