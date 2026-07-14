# -*- coding: utf-8 -*-
"""FalImageClient — FLUX.2 LoRA inference over the fal queue REST API.

Money-safe: ноль реальной сети/денег. Протокол (submit→poll→result) тестируется
через ``httpx.MockTransport``; построение payload — через мок ``_submit_and_poll``.
"""
import asyncio

import httpx
import pytest

from app.services.block_m_common.fal_image_client import FalImageClient


def test_requires_fal_key(monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    with pytest.raises(RuntimeError):
        FalImageClient()


def test_generate_builds_payload_and_parses_url(monkeypatch):
    captured = {}

    async def _fake_submit(self, model, payload):
        captured["model"] = model
        captured["payload"] = payload
        return {"images": [{"url": "https://fal/out.jpg"}]}

    monkeypatch.setattr(FalImageClient, "_submit_and_poll", _fake_submit)
    client = FalImageClient(api_key="k")
    res = asyncio.run(
        client.generate_flux2_lora(
            "a candid photo of sks woman, white shirt",
            "https://fal/lora.safetensors",
            lora_scale=1.15,
            guidance=3.0,
            image_size="portrait_4_3",
            steps=28,
        )
    )
    assert res == {"image_url": "https://fal/out.jpg", "cost_usd": 0.02}
    assert captured["model"] == "fal-ai/flux-2/lora"
    p = captured["payload"]
    assert p["prompt"] == "a candid photo of sks woman, white shirt"
    assert p["loras"] == [{"path": "https://fal/lora.safetensors", "scale": 1.15}]
    assert p["image_size"] == "portrait_4_3"
    assert p["guidance_scale"] == 3.0
    assert p["num_inference_steps"] == 28
    assert p["enable_safety_checker"] is False
    assert "seed" not in p  # seed omitted when not given


def test_generate_includes_seed_when_given(monkeypatch):
    captured = {}

    async def _fake_submit(self, model, payload):
        captured["payload"] = payload
        return {"images": [{"url": "https://fal/out.jpg"}]}

    monkeypatch.setattr(FalImageClient, "_submit_and_poll", _fake_submit)
    client = FalImageClient(api_key="k")
    asyncio.run(client.generate_flux2_lora("p", "lora", seed=5000))
    assert captured["payload"]["seed"] == 5000


def test_generate_empty_images_returns_empty_url(monkeypatch):
    async def _fake_submit(self, model, payload):
        return {"images": []}

    monkeypatch.setattr(FalImageClient, "_submit_and_poll", _fake_submit)
    client = FalImageClient(api_key="k")
    res = asyncio.run(client.generate_flux2_lora("p", "lora"))
    assert res["image_url"] == ""


def test_submit_and_poll_protocol_over_mock_transport():
    """submit → poll(status) → result через реальный httpx-стек (MockTransport)."""
    model = "fal-ai/flux-2/lora"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == f"/{model}":  # submit
            assert request.headers["authorization"] == "Key testkey"
            return httpx.Response(200, json={
                "status": "IN_QUEUE",
                "status_url": "https://queue.fal.run/job/status",
                "response_url": "https://queue.fal.run/job",
            })
        if path.endswith("/status"):
            return httpx.Response(200, json={"status": "COMPLETED"})
        return httpx.Response(200, json={"images": [{"url": "https://fal/final.jpg"}]})

    client = FalImageClient(api_key="testkey", transport=httpx.MockTransport(handler))
    res = asyncio.run(client.generate_flux2_lora("p", "lora"))
    assert res["image_url"] == "https://fal/final.jpg"


def test_submit_failure_raises():
    model = "fal-ai/flux-2/lora"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "bad request"})

    client = FalImageClient(api_key="k", transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError):
        asyncio.run(client.generate_flux2_lora("p", "lora"))


def test_job_failed_raises():
    model = "fal-ai/flux-2/lora"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == f"/{model}":
            return httpx.Response(200, json={
                "status": "IN_QUEUE",
                "status_url": "https://queue.fal.run/job/status",
                "response_url": "https://queue.fal.run/job",
            })
        if path.endswith("/status"):
            return httpx.Response(200, json={"status": "FAILED"})
        return httpx.Response(200, json={"error": "boom"})

    client = FalImageClient(api_key="k", transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError):
        asyncio.run(client.generate_flux2_lora("p", "lora"))
