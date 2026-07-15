# -*- coding: utf-8 -*-
"""DEV-3: money-preflight wired into fal/replicate/wavespeed clients.

Every submit chokepoint below MUST reject an empty/incomplete payload with an
AssertionError BEFORE touching the network. Deliberately no transport/mock is
supplied where possible — if the assert didn't fire before the network call,
these would try a real (and here, unreachable) HTTP request, not silently pass.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.services.block_m2_video.engines.engine_protocol import VideoRequest
from app.services.block_m2_video.engines.replicate_engine import ReplicateEngine
from app.services.block_m2_video.engines.replicate_seedance_engine import (
    ReplicateSeedanceEngine,
)
from app.services.block_m2_video.engines.wavespeed_http import WaveSpeedHTTPClient
from app.services.block_m2_video.engines.wavespeed_rife_client import (
    WaveSpeedRifeClient,
)
from app.services.block_m2_video.engines.wavespeed_spicy_engine import (
    WaveSpeedSpicyEngine,
)
from app.services.block_m_common.fal_image_client import FalImageClient
from app.services.block_m_common.faceswap_client import FaceSwapProvider
from app.services.block_m_common.lucataco_client import LucatacoClient
from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
from app.services.replicate_image_gen import _post_with_retry


def _fail_transport(_request: httpx.Request) -> httpx.Response:
    raise AssertionError("network call reached — preflight_check did not fire first")


def _img(tmp_path):
    p = tmp_path / "src.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nFAKE")
    return p


# ── fal ──────────────────────────────────────────────────────────────────────


def test_fal_submit_and_poll_rejects_empty_payload():
    client = FalImageClient(api_key="k", transport=httpx.MockTransport(_fail_transport))
    with pytest.raises(AssertionError):
        asyncio.run(client._submit_and_poll("fal-ai/flux-2/lora", {}))


def test_fal_generate_rejects_empty_prompt():
    client = FalImageClient(api_key="k", transport=httpx.MockTransport(_fail_transport))
    with pytest.raises(AssertionError):
        asyncio.run(client.generate_flux2_lora("", "https://fal/lora.safetensors"))


# ── replicate (block_m_common) ──────────────────────────────────────────────


def test_replicate_video_client_submit_rejects_empty_payload():
    client = ReplicateVideoClient(api_token="t")
    with pytest.raises(AssertionError):
        asyncio.run(client._submit("https://api.replicate.com/v1/predictions", {}))


def test_replicate_video_client_kling_rejects_empty_prompt():
    client = ReplicateVideoClient(api_token="t")
    with pytest.raises(AssertionError):
        asyncio.run(client.generate_kling_v21("https://img", ""))


def test_faceswap_provider_submit_rejects_empty_payload():
    client = FaceSwapProvider(api_token="t")
    with pytest.raises(AssertionError):
        asyncio.run(client._submit("https://api.replicate.com/v1/predictions", {}))


def test_lucataco_client_submit_rejects_empty_payload():
    client = LucatacoClient(api_token="t")
    with pytest.raises(AssertionError):
        asyncio.run(client._submit("https://api.replicate.com/v1/predictions", {}))


def test_lucataco_client_swap_rejects_empty_target_image():
    client = LucatacoClient(api_token="t")
    with pytest.raises(AssertionError):
        asyncio.run(client.swap("data:image/jpeg;base64,AA==", ""))


# ── replicate (block_m2_video engines) ──────────────────────────────────────


def test_replicate_engine_submit_with_retry_rejects_empty_payload():
    engine = ReplicateEngine(api_token="t")
    with pytest.raises(AssertionError):
        asyncio.run(engine._submit_with_retry({}))


def test_replicate_engine_generate_rejects_empty_prompt(tmp_path):
    engine = ReplicateEngine(api_token="t")
    req = VideoRequest(
        persona_id="p1", persona_name="Vera", input_image_path=_img(tmp_path), prompt=""
    )
    with pytest.raises(AssertionError):
        asyncio.run(engine.generate(req))


def test_replicate_seedance_engine_submit_with_retry_rejects_empty_payload():
    engine = ReplicateSeedanceEngine(api_token="t")
    with pytest.raises(AssertionError):
        asyncio.run(engine._submit_with_retry({}))


def test_replicate_seedance_engine_generate_rejects_empty_prompt(tmp_path):
    engine = ReplicateSeedanceEngine(api_token="t")
    req = VideoRequest(
        persona_id="p1", persona_name="Vera", input_image_path=_img(tmp_path), prompt=""
    )
    with pytest.raises(AssertionError):
        asyncio.run(engine.generate(req))


# ── wavespeed ────────────────────────────────────────────────────────────────


def test_wavespeed_http_client_submit_with_retry_rejects_empty_payload():
    client = WaveSpeedHTTPClient(api_key="k")
    with pytest.raises(AssertionError):
        asyncio.run(
            client._submit_with_retry("https://api.wavespeed.ai/api/v3/x", {})
        )


def test_wavespeed_spicy_engine_generate_rejects_empty_prompt(tmp_path):
    engine = WaveSpeedSpicyEngine(api_key="k")
    req = VideoRequest(
        persona_id="p1", persona_name="Vera", input_image_path=_img(tmp_path), prompt=""
    )
    with pytest.raises(AssertionError):
        asyncio.run(engine.generate(req))


# ── replicate (sync urllib client, app/services/replicate_image_gen.py) ────


def test_replicate_image_gen_post_with_retry_rejects_empty_payload():
    with pytest.raises(AssertionError):
        _post_with_retry("https://api.replicate.com/v1/predictions", {}, "k")


def test_replicate_image_gen_post_with_retry_rejects_missing_prompt():
    with pytest.raises(AssertionError):
        _post_with_retry(
            "https://api.replicate.com/v1/predictions",
            {"input": {"aspect_ratio": "9:16"}},
            "k",
        )


def test_wavespeed_rife_client_interpolate_rejects_empty_video_url(tmp_path):
    async def _empty_url(_p):
        return ""

    p = tmp_path / "in.mp4"
    p.write_bytes(b"FAKEMP4")
    client = WaveSpeedRifeClient(api_key="k", uploader=_empty_url)
    with pytest.raises(AssertionError):
        asyncio.run(client.interpolate(p))
