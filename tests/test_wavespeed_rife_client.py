"""Задача 1 (TDD): WaveSpeedRifeClient — RIFE frame-interpolation client.

Reuses wavespeed submit/poll/download mechanics (extracted to wavespeed_http).
Money-safe: litterbox upload happens BEFORE the billable RIFE POST, so an
upload failure never creates a billable prediction.
"""
from pathlib import Path

import httpx
import pytest

from app.services.block_m2_video.engines.errors import (
    TransientVideoError, TerminalVideoError,
)
from app.services.block_m2_video.litterbox_uploader import LitterboxError
from app.services.block_m2_video.engines.wavespeed_rife_client import (
    WaveSpeedRifeClient, WaveSpeedTransientError, WaveSpeedEngineError,
)

_RIFE = "https://api.wavespeed.ai/api/v3/wavespeed-ai/rife"


def _mp4(tmp_path) -> Path:
    p = tmp_path / "in.mp4"
    p.write_bytes(b"FAKEMP4")
    return p


async def _ok_url(_p):
    return "https://litter.catbox.moe/x.mp4"


def _client(handler, dl_handler=None, uploader=None, **kw) -> WaveSpeedRifeClient:
    return WaveSpeedRifeClient(
        api_key="t",
        uploader=uploader or _ok_url,
        transport=httpx.MockTransport(handler),
        download_transport=httpx.MockTransport(
            dl_handler or (lambda req: httpx.Response(200, content=b"SMOOTHMP4"))
        ),
        **kw,
    )


def _created(req: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"code": 200, "data": {
        "id": "r1", "status": "created",
        "urls": {"get": "https://api.wavespeed.ai/api/v3/predictions/r1/result"},
    }})


def _completed(req: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"data": {
        "status": "completed", "outputs": ["https://cdn/smooth.mp4"]}})


def test_errors_subclass_shared_bases():
    assert issubclass(WaveSpeedTransientError, TransientVideoError)
    assert issubclass(WaveSpeedEngineError, TerminalVideoError)


@pytest.mark.asyncio
async def test_interpolate_success_downloads_smoothed_mp4(tmp_path):
    def handler(req):
        return _created(req) if req.method == "POST" else _completed(req)

    cli = _client(handler, dl_handler=lambda r: httpx.Response(200, content=b"REALSMOOTH"),
                  backoff_base=0.0)
    out = await cli.interpolate(_mp4(tmp_path))
    assert out.exists()
    assert out.read_bytes() == b"REALSMOOTH"
    assert out != _mp4(tmp_path)  # smoothed file is a distinct path


@pytest.mark.asyncio
async def test_payload_carries_video_url_and_num_frames(tmp_path):
    seen = {}
    def handler(req):
        if req.method == "POST":
            import json
            seen["body"] = json.loads(req.content)
            return _created(req)
        return _completed(req)

    cli = _client(handler, backoff_base=0.0)
    await cli.interpolate(_mp4(tmp_path))
    assert seen["body"]["video"] == "https://litter.catbox.moe/x.mp4"
    assert seen["body"]["num_frames"] == 1   # x2 == num_frames:1 (confirmed by spike)


@pytest.mark.asyncio
async def test_num_frames_override_passes_through(tmp_path):
    seen = {}
    def handler(req):
        if req.method == "POST":
            import json
            seen["body"] = json.loads(req.content)
            return _created(req)
        return _completed(req)

    cli = _client(handler, backoff_base=0.0)
    await cli.interpolate(_mp4(tmp_path), num_frames=3)
    assert seen["body"]["num_frames"] == 3


@pytest.mark.asyncio
async def test_429_exhausted_raises_transient(tmp_path):
    def handler(req):
        if req.method == "POST":
            return httpx.Response(429, json={"message": "rate limited"})
        return _completed(req)

    cli = _client(handler, max_retries=2, backoff_base=0.0)
    with pytest.raises(WaveSpeedTransientError):
        await cli.interpolate(_mp4(tmp_path))


@pytest.mark.asyncio
async def test_submit_4xx_is_terminal_not_retried(tmp_path):
    posts = {"n": 0}
    def handler(req):
        if req.method == "POST":
            posts["n"] += 1
            return httpx.Response(400, json={"message": "bad request"})
        return _completed(req)

    cli = _client(handler, max_retries=5, backoff_base=0.0)
    with pytest.raises(WaveSpeedEngineError):
        await cli.interpolate(_mp4(tmp_path))
    assert posts["n"] == 1   # MONEY: 4xx never retried


@pytest.mark.asyncio
async def test_poll_failed_status_is_terminal(tmp_path):
    def handler(req):
        if req.method == "POST":
            return _created(req)
        return httpx.Response(200, json={"data": {"status": "failed", "error": "boom"}})

    cli = _client(handler, backoff_base=0.0)
    with pytest.raises(WaveSpeedEngineError):
        await cli.interpolate(_mp4(tmp_path))


@pytest.mark.asyncio
async def test_poll_timeout_is_terminal(tmp_path):
    def handler(req):
        return httpx.Response(200, json={"data": {"status": "processing"}})

    cli = _client(handler, backoff_base=0.0)
    with pytest.raises(WaveSpeedEngineError):
        await cli._poll("https://api.wavespeed.ai/api/v3/predictions/r1/result", max_wait=0)


@pytest.mark.asyncio
async def test_poll_5xx_retried_without_resubmit(tmp_path):
    counts = {"post": 0, "get": 0}
    def handler(req):
        if req.method == "POST":
            counts["post"] += 1
            return _created(req)
        counts["get"] += 1
        if counts["get"] <= 2:
            return httpx.Response(503, json={"message": "upstream"})
        return _completed(req)

    cli = _client(handler, backoff_base=0.0)
    out = await cli.interpolate(_mp4(tmp_path))
    assert out.exists()
    assert counts["post"] == 1   # MONEY: poll hiccup must NOT re-submit/re-bill
    assert counts["get"] >= 3


@pytest.mark.asyncio
async def test_litterbox_failure_raises_before_billable_post(tmp_path):
    posts = {"n": 0}
    def handler(req):
        if req.method == "POST":
            posts["n"] += 1
        return _created(req)

    async def boom(_p):
        raise LitterboxError("network error")

    cli = _client(handler, uploader=boom, backoff_base=0.0)
    with pytest.raises(LitterboxError):
        await cli.interpolate(_mp4(tmp_path))
    assert posts["n"] == 0   # MONEY: no prediction created when hosting fails


@pytest.mark.asyncio
async def test_missing_input_file_raises_before_upload(tmp_path):
    uploads = {"n": 0}
    async def counting_up(_p):
        uploads["n"] += 1
        return "https://litter/x.mp4"

    def handler(req):
        return _created(req) if req.method == "POST" else _completed(req)

    cli = _client(handler, uploader=counting_up, backoff_base=0.0)
    with pytest.raises(WaveSpeedEngineError):
        await cli.interpolate(tmp_path / "nope.mp4")
    assert uploads["n"] == 0
