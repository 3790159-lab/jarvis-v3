from pathlib import Path

from app.services.block_m2_video.engines.engine_protocol import VideoRequest
from app.services.block_m2_video.engines.errors import (
    TransientVideoError, TerminalVideoError,
)


def test_video_request_has_resolution_and_negative_prompt_defaults():
    r = VideoRequest(
        persona_id="p", persona_name="n",
        input_image_path=Path("x.jpg"), prompt="move",
    )
    assert r.resolution == "720p"
    assert r.negative_prompt == ""
    assert r.seconds == 5


def test_error_bases_are_distinct_runtimeerrors():
    assert issubclass(TransientVideoError, RuntimeError)
    assert issubclass(TerminalVideoError, RuntimeError)
    assert not issubclass(TransientVideoError, TerminalVideoError)


import httpx
import pytest

from app.services.block_m2_video.engines.wavespeed_spicy_engine import (
    WaveSpeedSpicyEngine, WaveSpeedTransientError, WaveSpeedEngineError,
)
from app.services.block_m2_video.engines.errors import (
    TransientVideoError, TerminalVideoError,
)


def _img(tmp_path) -> Path:
    p = tmp_path / "src.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0FAKEJPEG")
    return p


def _engine(handler, dl_handler=None, **kw) -> WaveSpeedSpicyEngine:
    return WaveSpeedSpicyEngine(
        api_key="t",
        transport=httpx.MockTransport(handler),
        download_transport=httpx.MockTransport(dl_handler or (lambda req: httpx.Response(200, content=b"MP4DATA"))),
        **kw,
    )


def test_wavespeed_errors_subclass_shared_bases():
    assert issubclass(WaveSpeedTransientError, TransientVideoError)
    assert issubclass(WaveSpeedEngineError, TerminalVideoError)


@pytest.mark.asyncio
async def test_generate_success(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            return httpx.Response(200, json={"code": 200, "data": {"id": "v1", "status": "created", "urls": {"get": "https://api.wavespeed.ai/api/v3/predictions/v1/result"}}})
        return httpx.Response(200, json={"data": {"status": "completed", "outputs": ["https://cdn/x.mp4"]}})

    from app.services.block_m2_video.engines.engine_protocol import VideoRequest
    eng = _engine(handler)
    res = await eng.generate(VideoRequest(
        persona_id="swapbatch_1", persona_name="b",
        input_image_path=_img(tmp_path), prompt="move", seconds=15, resolution="720p",
    ))
    assert res.output_path.exists()
    assert res.cost_usd == pytest.approx(1.50)
    assert res.engine == "wavespeed_spicy"
    assert res.seconds == 15


@pytest.mark.asyncio
async def test_generate_429_exhausted_raises_transient(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            return httpx.Response(429, json={"message": "rate limited"})
        return httpx.Response(200, json={"data": {"status": "completed", "outputs": ["x"]}})

    from app.services.block_m2_video.engines.engine_protocol import VideoRequest
    eng = _engine(handler, max_retries=2, backoff_base=0.0)
    with pytest.raises(WaveSpeedTransientError):
        await eng.generate(VideoRequest(
            persona_id="p", persona_name="b",
            input_image_path=_img(tmp_path), prompt="m", seconds=10,
        ))


@pytest.mark.asyncio
async def test_generate_failed_status_is_terminal(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            return httpx.Response(200, json={"data": {"id": "v", "status": "created", "urls": {"get": "https://api.wavespeed.ai/api/v3/predictions/v/result"}}})
        return httpx.Response(200, json={"data": {"status": "failed", "error": "boom"}})

    from app.services.block_m2_video.engines.engine_protocol import VideoRequest
    eng = _engine(handler)
    with pytest.raises(WaveSpeedEngineError):
        await eng.generate(VideoRequest(
            persona_id="p", persona_name="b",
            input_image_path=_img(tmp_path), prompt="m", seconds=5,
        ))


@pytest.mark.asyncio
async def test_submit_4xx_is_terminal_not_retried(tmp_path):
    posts = {"n": 0}
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            posts["n"] += 1
            return httpx.Response(400, json={"message": "bad request"})
        return httpx.Response(200, json={"data": {"status": "completed", "outputs": ["x"]}})

    from app.services.block_m2_video.engines.engine_protocol import VideoRequest
    eng = _engine(handler, max_retries=5, backoff_base=0.0)
    with pytest.raises(WaveSpeedEngineError):
        await eng.generate(VideoRequest(
            persona_id="p", persona_name="b",
            input_image_path=_img(tmp_path), prompt="m", seconds=5,
        ))
    assert posts["n"] == 1   # MONEY: 4xx never retried
