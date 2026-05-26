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
    DEFAULT_FRAMES,
    FPS,
    MAX_SECONDS,
    MIN_SECONDS,
    RunpodComfyEngine,
    RunpodComfyError,
    _WORKFLOW_FILE,
)
from app.services.block_m2_video.litterbox_uploader import LitterboxError
from app.services.block_m2_video.runpod.runpod_client import (
    GpuType,
    PodInfo,
    RunpodApiError,
    RunpodSupplyError,
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
    # Wired so cold-start guard tests can assert it is *not* awaited; a bare
    # MagicMock attribute would make assert_not_awaited a no-op.
    client.execute_command = AsyncMock()
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
            _json_response({"system": {"os": "linux"}}),  # /system_stats health check
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
    # First GET is the /system_stats health check; subsequent calls (the
    # /history poll) fail with HTTP 500 — generation must abort, but
    # stop_pod still has to fire from the finally block.
    http.get = AsyncMock(
        side_effect=[
            _json_response({"system": {"os": "linux"}}),
            _json_response({"err": "boom"}, status_code=500),
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
        prompt="walking",
    )
    with pytest.raises(RunpodComfyError):
        await engine.generate(req)
    client.stop_pod.assert_awaited_once_with("pod_abc")


# ── B-48: cold-start must not shell into the pod (no podExec auto-start) ─────


@pytest.mark.anyio
async def test_generate_cold_start_polls_without_execute_command(tmp_path):
    """Cold start (ComfyUI not yet up) must NOT call execute_command/podExec.

    The pod template's startup CMD launches ComfyUI; the engine only polls
    /system_stats until it answers. Guard for B-48 (podExec auto-start
    fallback removed): a down-then-up probe sequence must succeed by polling
    alone, never shelling into the pod.
    """
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
            # /system_stats: first probe → DOWN (cold start)
            httpx.ConnectError("conn refused"),
            # /system_stats: second probe (poll loop) → UP
            _json_response({"system": {"os": "linux"}}),
            # /history poll → completed
            _json_response(history_entry),
            # /view
            _bytes_response(mp4_bytes),
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
        prompt="walking",
        seed=7,
        seconds=5,
    )
    result = await engine.generate(req)

    assert isinstance(result, VideoResult)  # cold start still succeeds via polling
    client.execute_command.assert_not_awaited()


# ── _find_or_start_pod: pod discovery branches ───────────────────────────────


def _pod(
    pod_id: str,
    status: str,
    last_change: str | None = None,
    name: str = "jarvis-m2-x",
) -> PodInfo:
    return PodInfo.model_construct(
        id=pod_id,
        name=name,
        desired_status=status,
        last_status_change=last_change,
    )


@pytest.mark.anyio
async def test_find_or_start_pod_reuses_newest_running_pod():
    """When multiple RUNNING pods exist, pick the newest by last_status_change."""
    old_running = _pod("pod_old", "RUNNING", "2026-05-10T10:00:00Z")
    new_running = _pod("pod_new", "RUNNING", "2026-05-15T10:00:00Z")
    other_pod = _pod("pod_other", "RUNNING", "2026-05-14T10:00:00Z", name="unrelated")

    client = MagicMock()
    client.list_pods = AsyncMock(return_value=[old_running, other_pod, new_running])
    client.aclose = AsyncMock()

    engine = RunpodComfyEngine(config=_make_config(), client=client)
    pod, pod_id, reused = await engine._find_or_start_pod(client, "gen_test")

    assert reused is True
    assert pod_id == "pod_new"
    assert pod is new_running
    # Should not have tried to spawn or resume.
    assert not hasattr(client, "start_pod") or not client.start_pod.called  # type: ignore[truthy-function]


@pytest.mark.anyio
async def test_find_or_start_pod_skips_exited_candidates():
    """Only RUNNING pods are reuse candidates; EXITED pods are never resumed.

    RunPod runs the video-gen template's startCmd / bootstrap.sh (ffmpeg,
    opencv, sqlalchemy, ...) only on initial creation, NOT on resume — so a
    resumed EXITED pod never has ComfyUI alive and the engine polls until it
    times out (the B-48 regression seen in the Day-5 E2E test). When a
    RUNNING pod is present we reuse it; when only EXITED pods exist we skip
    them and spawn a fresh pod (which guarantees the bootstrap runs).
    """
    running = _pod("pod_run", "RUNNING", "2026-05-15T10:00:00Z")
    # An EXITED pod that is *newer* than the RUNNING one — must still be
    # ignored, proving we filter on status, not just recency.
    exited_newer = _pod("pod_dead", "EXITED", "2026-05-20T10:00:00Z")

    client = MagicMock()
    client.list_pods = AsyncMock(return_value=[exited_newer, running])
    client.resume_pod = AsyncMock()
    client.start_pod = AsyncMock()
    client.aclose = AsyncMock()

    engine = RunpodComfyEngine(config=_make_config(), client=client)
    pod, pod_id, reused = await engine._find_or_start_pod(client, "gen_mix")

    assert reused is True
    assert pod_id == "pod_run"
    assert pod is running
    client.resume_pod.assert_not_awaited()
    client.start_pod.assert_not_awaited()

    # With ONLY EXITED candidates, skip resume entirely and spawn fresh.
    spawned = _pod("pod_fresh", "RUNNING", "2026-05-20T11:00:00Z")
    ready = _pod("pod_fresh", "RUNNING", "2026-05-20T11:00:30Z")
    client2 = MagicMock()
    client2.list_pods = AsyncMock(return_value=[exited_newer])
    client2.resume_pod = AsyncMock()
    client2.start_pod = AsyncMock(return_value=spawned)
    client2.wait_for_ready = AsyncMock(return_value=ready)
    client2.aclose = AsyncMock()

    engine2 = RunpodComfyEngine(config=_make_config(), client=client2)
    pod2, pod_id2, reused2 = await engine2._find_or_start_pod(client2, "gen_dead")

    assert reused2 is False
    assert pod_id2 == "pod_fresh"
    assert pod2 is ready
    client2.resume_pod.assert_not_awaited()
    client2.start_pod.assert_awaited_once()
    assert client2.start_pod.await_args.kwargs.get("name") == "jarvis-m2-gen_dead"


@pytest.mark.anyio
async def test_find_or_start_pod_retries_on_supply_constraint(monkeypatch):
    """Fresh spawn hitting SUPPLY_CONSTRAINT retries instead of failing.

    Day-4-task-3: when no GPU is free, start_pod raises RunpodSupplyError.
    Sniper-style, the engine waits the nominal interval and retries, and
    succeeds once supply appears (here on the 3rd attempt).
    """
    import app.services.block_m2_video.engines.runpod_comfy_engine as engine_mod

    sleep_mock = AsyncMock()
    monkeypatch.setattr(engine_mod.asyncio, "sleep", sleep_mock)

    spawned = _pod("pod_fresh", "RUNNING", "2026-05-20T11:00:00Z")
    ready = _pod("pod_fresh", "RUNNING", "2026-05-20T11:00:30Z")

    client = MagicMock()
    client.list_pods = AsyncMock(return_value=[])  # nothing to reuse
    client.resume_pod = AsyncMock()
    client.start_pod = AsyncMock(
        side_effect=[
            RunpodSupplyError("SUPPLY_CONSTRAINT: no GPUs available"),
            RunpodSupplyError("SUPPLY_CONSTRAINT: no GPUs available"),
            spawned,
        ]
    )
    client.wait_for_ready = AsyncMock(return_value=ready)
    client.aclose = AsyncMock()

    engine = RunpodComfyEngine(config=_make_config(), client=client)
    pod, pod_id, reused = await engine._find_or_start_pod(client, "gen_supply")

    assert reused is False
    assert pod_id == "pod_fresh"
    assert pod is ready
    assert client.start_pod.await_count == 3  # 2 failures, success on the 3rd
    # Backoff: slept once after each failed attempt, at the 20s interval.
    assert sleep_mock.await_count == 2
    for call in sleep_mock.await_args_list:
        assert call.args[0] == 20.0
    client.resume_pod.assert_not_awaited()


@pytest.mark.anyio
async def test_find_or_start_pod_supply_timeout(monkeypatch):
    """Persistent SUPPLY_CONSTRAINT raises RunpodSupplyError after max attempts.

    The final error names the attempt count so the timeout is diagnosable
    rather than an opaque failure.
    """
    import app.services.block_m2_video.engines.runpod_comfy_engine as engine_mod

    sleep_mock = AsyncMock()
    monkeypatch.setattr(engine_mod.asyncio, "sleep", sleep_mock)

    client = MagicMock()
    client.list_pods = AsyncMock(return_value=[])
    client.resume_pod = AsyncMock()
    client.start_pod = AsyncMock(
        side_effect=RunpodSupplyError("SUPPLY_CONSTRAINT: no GPUs available")
    )
    client.wait_for_ready = AsyncMock()
    client.aclose = AsyncMock()

    engine = RunpodComfyEngine(
        config=_make_config(), client=client, supply_max_attempts=3
    )
    with pytest.raises(RunpodSupplyError) as excinfo:
        await engine._find_or_start_pod(client, "gen_nosupply")

    assert "3 attempts" in str(excinfo.value)  # attempt count in message
    assert client.start_pod.await_count == 3
    # Slept between attempts but not after the final failure.
    assert sleep_mock.await_count == 2
    client.wait_for_ready.assert_not_awaited()


# ── _ensure_comfyui_alive: health check + auto-start ─────────────────────────


@pytest.mark.anyio
async def test_ensure_comfyui_alive_returns_immediately_when_healthy():
    """If /system_stats returns 200 OK with JSON, no auto-start runs."""
    client = MagicMock()
    client.execute_command = AsyncMock()
    client.aclose = AsyncMock()

    http = MagicMock()
    http.aclose = AsyncMock()
    http.get = AsyncMock(
        return_value=_json_response({"system": {"os": "linux"}}, status_code=200)
    )

    engine = RunpodComfyEngine(
        config=_make_config(), client=client, http_client=http
    )
    await engine._ensure_comfyui_alive("pod_abc", "http://test-pod:8188")

    client.execute_command.assert_not_awaited()
    http.get.assert_awaited_once()


@pytest.mark.anyio
async def test_ensure_comfyui_alive_polls_until_alive_without_exec(monkeypatch):
    """First probe down, second up → returns by polling, never shells in.

    Guard for B-48: the podExec auto-start fallback was removed, so a
    not-yet-ready pod must be handled by re-polling /system_stats alone.
    """
    # Avoid real sleeps inside the polling loop.
    async def _no_sleep(*_a, **_kw) -> None:
        return None

    import app.services.block_m2_video.engines.runpod_comfy_engine as engine_mod

    monkeypatch.setattr(engine_mod.asyncio, "sleep", _no_sleep)

    client = MagicMock()
    client.execute_command = AsyncMock()
    client.aclose = AsyncMock()

    http = MagicMock()
    http.aclose = AsyncMock()
    # First probe = HTTP error (down); second probe = alive.
    http.get = AsyncMock(
        side_effect=[
            httpx.ConnectError("conn refused"),
            _json_response({"system": {"os": "linux"}}, status_code=200),
        ]
    )

    engine = RunpodComfyEngine(
        config=_make_config(), client=client, http_client=http
    )
    await engine._ensure_comfyui_alive("pod_abc", "http://test-pod:8188")

    client.execute_command.assert_not_awaited()
    assert http.get.await_count == 2


# ── _build_workflow: PainterI2VAdvanced.length parameterization ──────────────


def _build_workflow_request(tmp_path: Path, **overrides) -> VideoRequest:
    """Construct a VideoRequest suitable for direct _build_workflow() calls.

    _build_workflow() does not run _validate_request, so the image path is
    only referenced as a dataclass field — no file needed on disk.
    """
    defaults: dict = dict(
        persona_id="p1",
        persona_name="Test",
        input_image_path=tmp_path / "unused.png",
        prompt="a cinematic clip",
    )
    defaults.update(overrides)
    return VideoRequest(**defaults)


def _make_engine_for_workflow(tmp_path: Path) -> RunpodComfyEngine:
    client = MagicMock()
    client.aclose = AsyncMock()
    return RunpodComfyEngine(
        config=_make_config(), client=client, output_dir=tmp_path
    )


def test_build_workflow_default_length(tmp_path):
    # When the caller doesn't request a custom length (seconds is falsy), the
    # workflow's built-in default frames count must be preserved untouched.
    engine = _make_engine_for_workflow(tmp_path)
    req = _build_workflow_request(tmp_path, seconds=0)
    workflow = engine._build_workflow("input.png", req)
    assert workflow["15"]["inputs"]["length"] == DEFAULT_FRAMES == 121


def test_build_workflow_seconds_3(tmp_path):
    engine = _make_engine_for_workflow(tmp_path)
    req = _build_workflow_request(tmp_path, seconds=3)
    workflow = engine._build_workflow("input.png", req)
    assert workflow["15"]["inputs"]["length"] == 63  # 3 * 21


def test_build_workflow_seconds_10(tmp_path):
    engine = _make_engine_for_workflow(tmp_path)
    req = _build_workflow_request(tmp_path, seconds=10)
    workflow = engine._build_workflow("input.png", req)
    assert workflow["15"]["inputs"]["length"] == 210  # 10 * 21


def test_build_workflow_clamps_below_min(tmp_path):
    # 0.1 s → 2 frames, below the 21-frame floor → clamp to 21.
    engine = _make_engine_for_workflow(tmp_path)
    req = _build_workflow_request(tmp_path, seconds=0.1)
    workflow = engine._build_workflow("input.png", req)
    assert workflow["15"]["inputs"]["length"] == int(MIN_SECONDS * FPS) == 21


def test_build_workflow_clamps_above_max(tmp_path):
    # 30 s → 630 frames, above the 315-frame ceiling → clamp to 315.
    engine = _make_engine_for_workflow(tmp_path)
    req = _build_workflow_request(tmp_path, seconds=30)
    workflow = engine._build_workflow("input.png", req)
    assert workflow["15"]["inputs"]["length"] == int(MAX_SECONDS * FPS) == 315


def test_build_workflow_rounds_fractional(tmp_path):
    # 2.5 s * 21 fps = 52.5 → 53 frames via half-up rounding. (Python's built-in
    # round() uses banker's rounding which would return 52 here — we use
    # int(x + 0.5) in the engine to get the expected half-up behavior.)
    engine = _make_engine_for_workflow(tmp_path)
    req = _build_workflow_request(tmp_path, seconds=2.5)
    workflow = engine._build_workflow("input.png", req)
    assert workflow["15"]["inputs"]["length"] == 53


# ── _build_workflow: positive-prompt injection (node 7) ──────────────────────


def test_workflow_injection_with_custom_prompt(tmp_path):
    # request.prompt must be injected into the positive CLIPTextEncode (node 7).
    engine = _make_engine_for_workflow(tmp_path)
    req = _build_workflow_request(tmp_path, prompt="a fox runs through snow")
    workflow = engine._build_workflow("input.png", req)
    assert workflow["7"]["inputs"]["text"] == "a fox runs through snow"


def test_workflow_injection_falls_back_to_default(tmp_path):
    # With no prompt (None), node 7 keeps the workflow's built-in default text.
    engine = _make_engine_for_workflow(tmp_path)
    req = _build_workflow_request(tmp_path, prompt=None)
    workflow = engine._build_workflow("input.png", req)
    assert workflow["7"]["inputs"]["text"] == "A cinematic video clip"


def test_workflow_negative_prompt_unchanged(tmp_path):
    # Injecting a positive prompt must never touch the negative node (8).
    engine = _make_engine_for_workflow(tmp_path)
    base = json.loads(_WORKFLOW_FILE.read_text(encoding="utf-8"))
    req = _build_workflow_request(tmp_path, prompt="a fox runs through snow")
    workflow = engine._build_workflow("input.png", req)
    assert workflow["8"]["inputs"]["text"] == base["8"]["inputs"]["text"]
    assert workflow["7"]["inputs"]["text"] != base["7"]["inputs"]["text"]


# ── generate: litterbox upload integration ───────────────────────────────────


def _happy_path_engine_and_request(tmp_path):
    """Build the same happy-path engine+request as test_generate_happy_path."""
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
            _json_response({"system": {"os": "linux"}}),
            _json_response(history_entry),
            _bytes_response(mp4_bytes),
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
    return engine, req


@pytest.mark.anyio
async def test_generate_sets_public_url_on_successful_upload(tmp_path, monkeypatch):
    engine, req = _happy_path_engine_and_request(tmp_path)

    upload_mock = AsyncMock(return_value="https://litter.catbox.moe/x.mp4")
    monkeypatch.setattr(
        "app.services.block_m2_video.engines.runpod_comfy_engine.upload_to_litterbox",
        upload_mock,
    )

    result = await engine.generate(req)

    assert result.public_url == "https://litter.catbox.moe/x.mp4"
    upload_mock.assert_awaited_once()
    args, kwargs = upload_mock.await_args
    # The uploader is called with the local output path and retention="24h".
    assert kwargs.get("retention") == "24h"
    assert args[0] == result.output_path


@pytest.mark.anyio
async def test_generate_returns_result_without_public_url_on_upload_failure(
    tmp_path, monkeypatch, caplog
):
    engine, req = _happy_path_engine_and_request(tmp_path)

    upload_mock = AsyncMock(side_effect=LitterboxError("network down"))
    monkeypatch.setattr(
        "app.services.block_m2_video.engines.runpod_comfy_engine.upload_to_litterbox",
        upload_mock,
    )

    with caplog.at_level("WARNING"):
        result = await engine.generate(req)

    # Result returned, exception swallowed, public_url left as None.
    assert isinstance(result, VideoResult)
    assert result.public_url is None
    assert result.output_path.exists()
    upload_mock.assert_awaited_once()
    assert any(
        "Litterbox upload failed" in rec.message for rec in caplog.records
    )
