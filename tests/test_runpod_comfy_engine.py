# -*- coding: utf-8 -*-
"""Tests for :class:`RunpodComfyEngine`.

All RunPod and ComfyUI traffic is mocked via ``unittest.mock.AsyncMock``.
No real pods are spawned, no real HTTP requests are made.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import SecretStr

from app.services.block_m2_video.engines.engine_protocol import (
    VideoRequest,
    VideoResult,
)
from app.services.block_m2_video.engines.runpod_comfy_engine import (
    RunpodComfyEngine,
    RunpodComfyError,
)
from app.services.block_m2_video.runpod.runpod_client import (
    GpuType,
    PodInfo,
    RunpodApiError,
)
from app.services.block_m2_video.runpod.runpod_config import RunpodConfig


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_config(gpu_type_id: str = "GPU-PRIMARY", **overrides) -> RunpodConfig:
    defaults: dict = dict(
        api_key=SecretStr("rpa_test_key_xxxxxxxxxxxx"),
        api_endpoint="https://api.runpod.io/graphql",
        network_volume_id="vol_test_abcdef",
        datacenter="EU-TEST-1",
        gpu_type_id=gpu_type_id,
        gpu_fallback_id="GPU-FALLBACK",
        gpu_count=1,
        template_id=None,
        docker_image="runpod/test:latest",
        comfyui_port=8188,
        comfyui_auth_token=None,
        max_budget_usd_per_day=3.0,
        max_pod_lifetime_min=20,
        guardian_check_interval_sec=30,
        emergency_stop_enabled=True,
    )
    defaults.update(overrides)
    return RunpodConfig.model_construct(**defaults)


def _make_image(tmp_path: Path, name: str = "input.png") -> Path:
    p = tmp_path / name
    # Minimal PNG signature so any future mime sniffing is happy.
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 256)
    return p


def _json_response(payload: dict, status_code: int = 200) -> httpx.Response:
    request = httpx.Request("GET", "http://test-pod:8188/")
    return httpx.Response(
        status_code=status_code,
        content=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        request=request,
    )


def _bytes_response(content: bytes, status_code: int = 200) -> httpx.Response:
    request = httpx.Request("GET", "http://test-pod:8188/")
    return httpx.Response(status_code=status_code, content=content, request=request)


def _mock_pod(
    pod_id: str = "pod_abc",
    name: str = "jarvis-m2-test",
    status: str = "RUNNING",
    cost_per_hr: float | None = 1.59,
) -> PodInfo:
    return PodInfo.model_construct(
        id=pod_id,
        name=name,
        desired_status=status,
        cost_per_hr=cost_per_hr,
    )


def _mock_runpod_client(pod: PodInfo) -> MagicMock:
    client = MagicMock()
    client.list_pods = AsyncMock(return_value=[])
    client.start_pod = AsyncMock(return_value=pod)
    client.wait_for_ready = AsyncMock(return_value=pod)
    client.get_pod_public_url = AsyncMock(return_value="http://test-pod:8188")
    client.stop_pod = AsyncMock(return_value=True)
    client.aclose = AsyncMock()
    return client


# ── is_available ─────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_is_available_returns_false_when_runpod_unreachable():
    client = MagicMock()
    client.list_gpu_types = AsyncMock(side_effect=RunpodApiError("transport boom"))
    client.aclose = AsyncMock()
    engine = RunpodComfyEngine(config=_make_config(), client=client)
    assert await engine.is_available() is False
    client.list_gpu_types.assert_awaited_once()


@pytest.mark.anyio
async def test_is_available_returns_true_when_gpu_present():
    gpus = [
        GpuType.model_construct(id="GPU-PRIMARY", display_name="Primary"),
        GpuType.model_construct(id="GPU-OTHER", display_name="Other"),
    ]
    client = MagicMock()
    client.list_gpu_types = AsyncMock(return_value=gpus)
    client.aclose = AsyncMock()
    engine = RunpodComfyEngine(
        config=_make_config(gpu_type_id="GPU-PRIMARY"), client=client
    )
    assert await engine.is_available() is True


# ── generate: validation ─────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_generate_validates_image_path(tmp_path):
    client = MagicMock()
    client.aclose = AsyncMock()
    engine = RunpodComfyEngine(
        config=_make_config(), client=client, output_dir=tmp_path
    )
    req = VideoRequest(
        persona_id="p1",
        persona_name="Test",
        input_image_path=tmp_path / "missing.png",
        prompt="hi",
    )
    with pytest.raises(ValueError, match="does not exist"):
        await engine.generate(req)


# ── generate: happy path ─────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_generate_happy_path_with_mocks(tmp_path):
    image = _make_image(tmp_path)
    pod = _mock_pod()
    client = _mock_runpod_client(pod)

    history_entry = {
        "pid_xyz": {
            "outputs": {
                "30": {
                    "videos": [
                        {
                            "filename": "wan_output_00001.mp4",
                            "subfolder": "",
                            "type": "output",
                        }
                    ]
                }
            },
            "status": {"status_str": "success", "completed": True},
        }
    }
    mp4_bytes = b"\x00\x00\x00\x18ftypisom" + b"x" * (200 * 1024)

    http = MagicMock()
    http.aclose = AsyncMock()
    http.post = AsyncMock(
        side_effect=[
            _json_response({"name": "input.png", "subfolder": "", "type": "input"}),
            _json_response({"prompt_id": "pid_xyz", "number": 0, "node_errors": {}}),
        ]
    )
    http.get = AsyncMock(
        side_effect=[
            _json_response(history_entry),  # poll → completed immediately
            _bytes_response(mp4_bytes),  # /view
        ]
    )

    engine = RunpodComfyEngine(
        config=_make_config(),
        client=client,
        http_client=http,
        output_dir=tmp_path / "out",
        poll_interval_sec=0.0,
    )
    req = VideoRequest(
        persona_id="p1",
        persona_name="Test",
        input_image_path=image,
        prompt="walking on the beach",
        seed=42,
        seconds=5,
    )
    result = await engine.generate(req)

    assert isinstance(result, VideoResult)
    assert result.engine == "runpod_comfy"
    assert result.model == "wan2.2-remix-i2v-14b"
    assert result.seed == 42
    assert result.persona_id == "p1"
    assert result.prompt == "walking on the beach"
    assert result.seconds == 5
    assert result.output_path.exists()
    assert result.output_path.read_bytes() == mp4_bytes
    assert result.extra == {
        "prompt_id": "pid_xyz",
        "pod_id": "pod_abc",
        "workflow_version": "v20",
    }
    assert result.duration_sec >= 0
    assert result.cost_usd >= 0
    assert result.generation_id.startswith("gen_")
    client.start_pod.assert_awaited_once()
    client.wait_for_ready.assert_awaited_once_with("pod_abc", timeout_sec=600)
    client.stop_pod.assert_awaited_once_with("pod_abc")

    # Verify the workflow we submitted carried the uploaded filename and seed.
    submit_call = http.post.await_args_list[1]
    submitted = submit_call.kwargs["json"]["prompt"]
    assert submitted["5"]["inputs"]["image"] == "input.png"
    assert submitted["7"]["inputs"]["text"] == "walking on the beach"
    assert submitted["16"]["inputs"]["noise_seed"] == 42
    assert submitted["18"]["inputs"]["noise_seed"] == 42


# ── generate: failure path still stops pod ───────────────────────────────────


@pytest.mark.anyio
async def test_generate_stops_pod_on_failure(tmp_path):
    image = _make_image(tmp_path)
    pod = _mock_pod()
    client = _mock_runpod_client(pod)

    http = MagicMock()
    http.aclose = AsyncMock()
    http.post = AsyncMock(
        side_effect=[
            _json_response({"name": "input.png"}),
            _json_response({"prompt_id": "pid_xyz", "node_errors": {}}),
        ]
    )
    # Polling fails with an HTTP 500 — generation must abort, but stop_pod
    # still has to fire from the finally block.
    http.get = AsyncMock(
        return_value=_json_response({"err": "boom"}, status_code=500)
    )

    engine = RunpodComfyEngine(
        config=_make_config(),
        client=client,
        http_client=http,
        output_dir=tmp_path / "out",
        poll_interval_sec=0.0,
    )
    req = VideoRequest(
        persona_id="p1",
        persona_name="Test",
        input_image_path=image,
        prompt="walking",
    )
    with pytest.raises(RunpodComfyError):
        await engine.generate(req)
    client.stop_pod.assert_awaited_once_with("pod_abc")
