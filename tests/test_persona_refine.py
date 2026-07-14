# -*- coding: utf-8 -*-
"""Post-gen img2img refiner (magic-image-refiner) for persona photos.

Spike verdict (2026-07-14): the persona LoRA bakes a glam/waxy bias into its
weights that inference knobs (scale/seed) cannot beat; a low-denoise img2img
refiner on the OUTPUT restores natural skin while keeping identity+composition.
Winner: batouresearch/magic-image-refiner (ControlNet-tile), creativity ~0.30,
resemblance 0.8. These tests pin the client method, the PhotoGenerator refine
branch (success + graceful fallback), and the brand.md flag resolver.

$0 — every network/pay call is mocked.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.block_m1_persona.photo_generator import PhotoGenerator
from tests.test_block_m13_photo_generator import _make_generator


# ── ReplicateVideoClient.refine_image ────────────────────────────────────────

@pytest.mark.anyio
async def test_refine_image_returns_url_and_cost():
    from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
    client = ReplicateVideoClient(api_token="tok")
    with patch.object(client, "_get_latest_version", new=AsyncMock(return_value="v")), \
         patch.object(client, "_run_prediction", new_callable=AsyncMock) as mock:
        mock.return_value = ["https://refined.example/out.png"]
        result = await client.refine_image("https://in.jpg", creativity=0.30, resemblance=0.8)
    assert result["image_url"] == "https://refined.example/out.png"
    assert result["cost_usd"] > 0


@pytest.mark.anyio
async def test_refine_image_string_output_used_directly():
    from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
    client = ReplicateVideoClient(api_token="tok")
    with patch.object(client, "_get_latest_version", new=AsyncMock(return_value="v")), \
         patch.object(client, "_run_prediction", new_callable=AsyncMock) as mock:
        mock.return_value = "https://refined.example/out.png"
        result = await client.refine_image("https://in.jpg")
    assert result["image_url"] == "https://refined.example/out.png"


@pytest.mark.anyio
async def test_refine_image_sends_magic_refiner_model_and_knobs():
    from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
    client = ReplicateVideoClient(api_token="tok")
    captured = {}

    async def capture(model, payload, **kw):
        captured["model"] = model
        captured["payload"] = payload
        return ["u"]

    with patch.object(client, "_get_latest_version", new=AsyncMock(return_value="v")), \
         patch.object(client, "_run_prediction", side_effect=capture):
        await client.refine_image("https://in.jpg", creativity=0.33, resemblance=0.8)

    assert captured["model"] == "batouresearch/magic-image-refiner"
    inp = captured["payload"]["input"]
    assert inp["image"] == "https://in.jpg"
    assert inp["creativity"] == 0.33
    assert inp["resemblance"] == 0.8


# ── community-model submission MUST use the version endpoint, not model endpoint
# Root cause of the live fallback (2026-07-14): `_run_prediction` hit
# /v1/models/{owner}/{name}/predictions which 404s for community models like
# batouresearch/*; the fix routes versioned submits to /v1/predictions.

@pytest.mark.anyio
async def test_run_prediction_with_version_uses_predictions_endpoint():
    from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
    client = ReplicateVideoClient(api_token="tok")
    captured = {}

    async def fake_submit(url, body):
        captured["url"] = url
        captured["body"] = body
        return "pid1"

    async def fake_poll(pid):
        return ["https://out.png"]

    with patch.object(client, "_submit", side_effect=fake_submit), \
         patch.object(client, "_poll", side_effect=fake_poll):
        out = await client._run_prediction("owner/model", {"input": {"x": 1}}, version="ver123")

    assert captured["url"].endswith("/predictions")
    assert "/models/" not in captured["url"]
    assert captured["body"]["version"] == "ver123"
    assert captured["body"]["input"] == {"x": 1}
    assert out == ["https://out.png"]


@pytest.mark.anyio
async def test_run_prediction_without_version_keeps_model_endpoint():
    """Back-compat: official models (no version) still use the model endpoint."""
    from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
    client = ReplicateVideoClient(api_token="tok")
    captured = {}

    async def fake_submit(url, body):
        captured["url"] = url
        return "pid1"

    async def fake_poll(pid):
        return "ok"

    with patch.object(client, "_submit", side_effect=fake_submit), \
         patch.object(client, "_poll", side_effect=fake_poll):
        await client._run_prediction("black-forest-labs/flux-dev-lora", {"input": {}})

    assert captured["url"].endswith("/models/black-forest-labs/flux-dev-lora/predictions")


@pytest.mark.anyio
async def test_refine_image_resolves_version_and_submits_to_predictions_endpoint():
    """refine_image resolves the model's latest version and submits via the
    /v1/predictions endpoint (the 404-safe path for community models)."""
    from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
    client = ReplicateVideoClient(api_token="tok")
    captured = {}

    async def fake_submit(url, body):
        captured["url"] = url
        captured["body"] = body
        return "pid"

    async def fake_poll(pid):
        return "https://refined.png"

    with patch.object(client, "_get_latest_version", new=AsyncMock(return_value="verABC")), \
         patch.object(client, "_submit", side_effect=fake_submit), \
         patch.object(client, "_poll", side_effect=fake_poll):
        res = await client.refine_image("https://in.jpg", creativity=0.30, resemblance=0.8)

    assert captured["url"].endswith("/predictions")
    assert "/models/" not in captured["url"]
    assert captured["body"]["version"] == "verABC"
    assert captured["body"]["input"]["image"] == "https://in.jpg"
    assert res["image_url"] == "https://refined.png"


# ── PhotoGenerator.generate_photo — refine branch ────────────────────────────

@pytest.mark.anyio
async def test_generate_photo_refine_success_uses_refined_url_and_adds_cost():
    gen, client, _, _ = _make_generator(image_url="https://gen.jpg")
    client.refine_image = AsyncMock(
        return_value={"image_url": "https://refined.jpg", "cost_usd": 0.02})
    result = await gen.generate_photo(
        "persona_test01", "x", refine=True,
        refine_creativity=0.30, refine_resemblance=0.8)
    assert result["image_url"] == "https://refined.jpg"
    assert result["refined"] is True
    assert result["cost_usd"] == pytest.approx(0.04)  # 0.02 gen + 0.02 refine
    kw = client.refine_image.call_args.kwargs
    assert kw["creativity"] == 0.30
    assert kw["resemblance"] == 0.8


@pytest.mark.anyio
async def test_generate_photo_refine_receives_raw_generated_url():
    """Refiner must get the freshly generated (waxy) url as its input image."""
    gen, client, _, _ = _make_generator(image_url="https://gen-raw.jpg")
    client.refine_image = AsyncMock(
        return_value={"image_url": "https://refined.jpg", "cost_usd": 0.02})
    await gen.generate_photo("persona_test01", "x", refine=True)
    args, kwargs = client.refine_image.call_args
    assert "https://gen-raw.jpg" in (list(args) + list(kwargs.values()))


@pytest.mark.anyio
async def test_generate_photo_refine_fallback_returns_original_no_charge():
    """Refiner crash/timeout → original frame, refined=False, no post-flow break,
    no refine charge."""
    gen, client, _, tracker = _make_generator(image_url="https://gen.jpg")
    client.refine_image = AsyncMock(side_effect=RuntimeError("refiner 500"))
    result = await gen.generate_photo("persona_test01", "x", refine=True)
    assert result["image_url"] == "https://gen.jpg"   # original, not broken
    assert result["refined"] is False
    assert result["cost_usd"] == pytest.approx(0.02)   # gen only, refine not charged


@pytest.mark.anyio
async def test_generate_photo_refine_off_by_default_no_refine_call():
    gen, client, _, _ = _make_generator()
    client.refine_image = AsyncMock()
    result = await gen.generate_photo("persona_test01", "x")
    client.refine_image.assert_not_called()
    assert result.get("refined") in (False, None)


# ── brand_config.find_persona_media (flag source for /persona_photo) ──────────

def test_find_persona_media_returns_media_for_matching_persona_id():
    from app.services.brand_config import find_persona_media
    pm = find_persona_media("persona_68fb76b2")  # vera_ai_ua/brand.md
    assert pm is not None
    assert pm.get("persona_id") == "persona_68fb76b2"


def test_find_persona_media_none_for_unknown_persona():
    from app.services.brand_config import find_persona_media
    assert find_persona_media("persona_does_not_exist_zzz") is None


# ── refine_prompt threading (identity anchors in auto-refine, 2026-07-14) ─────

@pytest.mark.anyio
async def test_generate_photo_forwards_refine_prompt_to_refiner():
    """A per-persona refine_prompt (de-wax + identity anchors) must reach
    refine_image as its `prompt` kwarg; None keeps the neutral default."""
    gen, client, _, _ = _make_generator(image_url="https://gen.jpg")
    client.refine_image = AsyncMock(
        return_value={"image_url": "https://refined.jpg", "cost_usd": 0.02})
    anchor = "candid photo, grey-blue eyes, fair skin, same person"
    await gen.generate_photo("persona_test01", "x", refine=True, refine_prompt=anchor)
    assert client.refine_image.call_args.kwargs["prompt"] == anchor


@pytest.mark.anyio
async def test_generate_photo_refine_prompt_defaults_none():
    gen, client, _, _ = _make_generator(image_url="https://gen.jpg")
    client.refine_image = AsyncMock(
        return_value={"image_url": "https://refined.jpg", "cost_usd": 0.02})
    await gen.generate_photo("persona_test01", "x", refine=True)
    assert client.refine_image.call_args.kwargs["prompt"] is None


@pytest.mark.anyio
async def test_refine_image_forwards_custom_prompt_in_payload():
    from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
    client = ReplicateVideoClient(api_token="tok")
    captured = {}

    async def capture(model, payload, **kw):
        captured["payload"] = payload
        return ["u"]

    with patch.object(client, "_get_latest_version", new=AsyncMock(return_value="v")), \
         patch.object(client, "_run_prediction", side_effect=capture):
        await client.refine_image("https://in.jpg", prompt="grey-blue eyes, same person")
    assert captured["payload"]["input"]["prompt"] == "grey-blue eyes, same person"


def test_brand_persona_media_has_cr025_and_identity_anchors():
    """brand.md vera_ai_ua: refine default is cr0.25 + identity-anchor prompt."""
    from app.services.brand_config import find_persona_media
    pm = find_persona_media("persona_68fb76b2")
    assert pm is not None
    assert float(pm["refine_creativity"]) == 0.25
    rp = pm.get("refine_prompt", "")
    assert "grey-blue eyes" in rp and "fair skin" in rp and "same person" in rp
