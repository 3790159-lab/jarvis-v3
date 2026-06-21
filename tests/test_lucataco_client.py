import httpx
import pytest

from app.services.block_m_common.faceswap_client import PredictionFailed
from app.services.block_m_common.lucataco_client import LucatacoClient


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
