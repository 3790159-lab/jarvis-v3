# -*- coding: utf-8 -*-
from pathlib import Path
import json
import httpx
import pytest

from app.services.block_m2_video.engines.engine_protocol import VideoRequest
from app.services.block_m2_video.engines.errors import (
    TransientVideoError, TerminalVideoError,
)
from app.services.block_m2_video.engines.replicate_seedance_engine import (
    ReplicateSeedanceEngine, ReplicateSeedanceTransientError, ReplicateSeedanceEngineError,
)


def _img(tmp_path) -> Path:
    p = tmp_path / "src.jpg"; p.write_bytes(b"\xff\xd8\xff\xe0FAKE"); return p


def _engine(handler, dl_handler=None, **kw) -> ReplicateSeedanceEngine:
    return ReplicateSeedanceEngine(
        api_token="t",
        transport=httpx.MockTransport(handler),
        download_transport=httpx.MockTransport(dl_handler or (lambda r: httpx.Response(200, content=b"MP4"))),
        **kw,
    )


def test_errors_subclass_shared_bases():
    assert issubclass(ReplicateSeedanceTransientError, TransientVideoError)
    assert issubclass(ReplicateSeedanceEngineError, TerminalVideoError)


@pytest.mark.asyncio
async def test_generate_success_and_cost(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            body = json.loads(req.content)
            assert body["input"]["prompt"] == "move"        # prompt required
            assert body["input"]["image"].startswith("data:")  # data-URI
            assert body["input"]["duration"] == 5
            assert body["input"]["resolution"] == "1080p"
            return httpx.Response(201, json={"id": "p1", "status": "starting"})
        return httpx.Response(200, json={"status": "succeeded", "output": "https://cdn/x.mp4"})

    eng = _engine(handler, backoff_base=0.0)
    res = await eng.generate(VideoRequest(
        persona_id="p", persona_name="b", input_image_path=_img(tmp_path),
        prompt="move", seconds=5, resolution="1080p", mode="seedance",
    ))
    assert res.output_path.exists()
    assert res.cost_usd == pytest.approx(0.25)   # SEEDANCE_CAPS[("1080p",5)]
    assert res.engine == "replicate_seedance"


@pytest.mark.asyncio
async def test_429_exhausted_is_transient(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"detail": "rate"})
    eng = _engine(handler, max_retries=2, backoff_base=0.0)
    with pytest.raises(ReplicateSeedanceTransientError):
        await eng.generate(VideoRequest(
            persona_id="p", persona_name="b", input_image_path=_img(tmp_path),
            prompt="m", seconds=5, mode="seedance"))


@pytest.mark.asyncio
async def test_4xx_terminal_not_retried(tmp_path):
    posts = {"n": 0}
    def handler(req: httpx.Request) -> httpx.Response:
        posts["n"] += 1
        return httpx.Response(422, json={"detail": "bad"})
    eng = _engine(handler, max_retries=5, backoff_base=0.0)
    with pytest.raises(ReplicateSeedanceEngineError):
        await eng.generate(VideoRequest(
            persona_id="p", persona_name="b", input_image_path=_img(tmp_path),
            prompt="m", seconds=5, mode="seedance"))
    assert posts["n"] == 1   # MONEY: 4xx (≠429) никогда не ретраится


@pytest.mark.asyncio
async def test_failed_status_terminal(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            return httpx.Response(201, json={"id": "p", "status": "starting"})
        return httpx.Response(200, json={"status": "failed", "error": "boom"})
    eng = _engine(handler, backoff_base=0.0)
    with pytest.raises(ReplicateSeedanceEngineError):
        await eng.generate(VideoRequest(
            persona_id="p", persona_name="b", input_image_path=_img(tmp_path),
            prompt="m", seconds=5, mode="seedance"))
