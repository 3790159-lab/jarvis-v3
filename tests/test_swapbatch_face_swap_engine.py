# -*- coding: utf-8 -*-
"""Tests for :class:`FaceSwapEngine`. All RunPod + ComfyUI traffic mocked."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import SecretStr

from app.services.block_m2_face_swap.face_swap_engine import (
    FaceSwapEngine,
    FaceSwapError,
)
from app.services.block_m2_video.runpod.runpod_client import PodInfo, RunpodApiError
from app.services.block_m2_video.runpod.runpod_config import RunpodConfig


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_config() -> RunpodConfig:
    return RunpodConfig.model_construct(
        api_key=SecretStr("rpa_test_xxxxxxxxxxxx"),
        api_endpoint="https://api.runpod.io/graphql",
        network_volume_id="vol_test",
        datacenter="EU-TEST-1",
        gpu_type_id="GPU-PRIMARY",
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


def _make_image(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 1024)
    return p


def _json_response(payload: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        content=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        request=httpx.Request("GET", "http://test/"),
    )


def _bytes_response(content: bytes, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        content=content,
        request=httpx.Request("GET", "http://test/"),
    )


def _mock_pod() -> PodInfo:
    return PodInfo.model_construct(
        id="pod_abc", name="jarvis-m2-test",
        desired_status="RUNNING", cost_per_hr=1.59,
    )


def _mock_runpod_client(pods=None) -> MagicMock:
    pod = _mock_pod()
    client = MagicMock()
    client.list_pods = AsyncMock(return_value=pods if pods is not None else [pod])
    client.start_pod = AsyncMock(return_value=pod)
    client.resume_pod = AsyncMock(return_value=pod)
    client.wait_for_ready = AsyncMock(return_value=pod)
    client.get_pod_public_url = AsyncMock(return_value="http://test:8188")
    client.stop_pod = AsyncMock(return_value=True)
    client.execute_command = AsyncMock()
    client.aclose = AsyncMock()
    return client


def _success_history(prompt_id: str, filename: str) -> dict:
    return {
        prompt_id: {
            "status": {"completed": True, "status_str": "success"},
            "outputs": {
                "4": {
                    "images": [
                        {"filename": filename, "subfolder": "", "type": "output"}
                    ]
                }
            },
        }
    }


# ── happy path ───────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_swap_batch_reuses_pod_and_uploads_source_once(tmp_path):
    src = _make_image(tmp_path, "src.jpg")
    t1 = _make_image(tmp_path, "t1.jpg")
    t2 = _make_image(tmp_path, "t2.jpg")

    http = MagicMock(spec=httpx.AsyncClient)
    http.aclose = AsyncMock()
    http.get = AsyncMock(side_effect=[
        # system_stats (alive)
        _json_response({"system": "ok"}),
        # /object_info (reactor present)
        _json_response({"ReActorFaceSwap": {"info": "..."}}),
        # /history target 1 → success
        _json_response(_success_history("p1", "swap_out_0001.png")),
        # /view target 1 → bytes
        _bytes_response(b"\x89PNG" + b"\x00" * 20_000),
        # /history target 2 → success
        _json_response(_success_history("p2", "swap_out_0002.png")),
        # /view target 2 → bytes
        _bytes_response(b"\x89PNG" + b"\x00" * 20_000),
    ])
    http.post = AsyncMock(side_effect=[
        # upload source
        _json_response({"name": "src.jpg"}),
        # upload target 1
        _json_response({"name": "t1.jpg"}),
        # /prompt 1
        _json_response({"prompt_id": "p1"}),
        # upload target 2
        _json_response({"name": "t2.jpg"}),
        # /prompt 2
        _json_response({"prompt_id": "p2"}),
    ])

    client = _mock_runpod_client()
    engine = FaceSwapEngine(
        config=_make_config(), client=client,
        output_dir=tmp_path / "out", http_client=http,
        poll_interval_sec=0.0,
    )

    results = await engine.swap_batch(src, [t1, t2])
    assert len(results) == 2
    assert all(r is not None for r in results)
    assert results[0].exists() and results[1].exists()
    # Pod reused (RUNNING) → no start_pod call.
    client.start_pod.assert_not_awaited()
    # Stopped exactly once at end.
    client.stop_pod.assert_awaited_once_with("pod_abc")
    # /upload/image called 3× (1 source + 2 targets).
    upload_calls = [c for c in http.post.call_args_list
                    if "/upload/image" in c.args[0]]
    assert len(upload_calls) == 3


@pytest.mark.anyio
async def test_swap_batch_uses_reactor_opt_fallback_when_primary_missing(tmp_path):
    src = _make_image(tmp_path, "src.jpg")
    t1 = _make_image(tmp_path, "t1.jpg")

    submitted_workflow = {}

    async def fake_post(url, *args, **kwargs):
        if "/upload/image" in url:
            return _json_response({"name": "x.jpg"})
        if "/prompt" in url:
            submitted_workflow.update(kwargs.get("json", {}).get("prompt", {}))
            return _json_response({"prompt_id": "p1"})
        return _json_response({})

    http = MagicMock(spec=httpx.AsyncClient)
    http.aclose = AsyncMock()
    http.post = AsyncMock(side_effect=fake_post)
    http.get = AsyncMock(side_effect=[
        _json_response({"system": "ok"}),
        # /object_info: only the Opt variant is present.
        _json_response({"ReActorFaceSwapOpt": {"info": "..."}}),
        _json_response(_success_history("p1", "swap_out.png")),
        _bytes_response(b"\x89PNG" + b"\x00" * 20_000),
    ])

    engine = FaceSwapEngine(
        config=_make_config(), client=_mock_runpod_client(),
        output_dir=tmp_path / "out", http_client=http,
        poll_interval_sec=0.0,
    )
    results = await engine.swap_batch(src, [t1])
    assert results[0] is not None
    assert submitted_workflow["3"]["class_type"] == "ReActorFaceSwapOpt"


@pytest.mark.anyio
async def test_swap_batch_partial_failure_returns_none_for_failed_index(tmp_path):
    src = _make_image(tmp_path, "src.jpg")
    t1 = _make_image(tmp_path, "t1.jpg")
    t2 = _make_image(tmp_path, "t2.jpg")

    http = MagicMock(spec=httpx.AsyncClient)
    http.aclose = AsyncMock()
    http.get = AsyncMock(side_effect=[
        _json_response({"system": "ok"}),
        _json_response({"ReActorFaceSwap": {}}),
        _json_response(_success_history("p1", "swap_out_0001.png")),
        _bytes_response(b"\x89PNG" + b"\x00" * 20_000),
        # target 2: /history returns success but no image → engine raises
        _json_response({"p2": {"status": {"completed": True, "status_str": "success"}, "outputs": {}}}),
    ])
    http.post = AsyncMock(side_effect=[
        _json_response({"name": "src.jpg"}),  # source upload
        _json_response({"name": "t1.jpg"}),   # target 1 upload
        _json_response({"prompt_id": "p1"}),
        _json_response({"name": "t2.jpg"}),   # target 2 upload
        _json_response({"prompt_id": "p2"}),
    ])

    engine = FaceSwapEngine(
        config=_make_config(), client=_mock_runpod_client(),
        output_dir=tmp_path / "out", http_client=http,
        poll_interval_sec=0.0,
    )
    results = await engine.swap_batch(src, [t1, t2])
    assert results[0] is not None
    assert results[1] is None


@pytest.mark.anyio
async def test_swap_batch_empty_targets_returns_empty_list(tmp_path):
    src = _make_image(tmp_path, "src.jpg")
    engine = FaceSwapEngine(
        config=_make_config(), client=_mock_runpod_client(),
        output_dir=tmp_path / "out",
    )
    results = await engine.swap_batch(src, [])
    assert results == []


@pytest.mark.anyio
async def test_swap_batch_cancel_check_stops_loop(tmp_path):
    src = _make_image(tmp_path, "src.jpg")
    t1 = _make_image(tmp_path, "t1.jpg")
    t2 = _make_image(tmp_path, "t2.jpg")

    http = MagicMock(spec=httpx.AsyncClient)
    http.aclose = AsyncMock()
    http.get = AsyncMock(side_effect=[
        _json_response({"system": "ok"}),
        _json_response({"ReActorFaceSwap": {}}),
        _json_response(_success_history("p1", "swap_out.png")),
        _bytes_response(b"\x89PNG" + b"\x00" * 20_000),
    ])
    http.post = AsyncMock(side_effect=[
        _json_response({"name": "src.jpg"}),
        _json_response({"name": "t1.jpg"}),
        _json_response({"prompt_id": "p1"}),
    ])

    call_count = {"n": 0}

    def cancel_check():
        call_count["n"] += 1
        return call_count["n"] > 1  # cancel before second photo

    engine = FaceSwapEngine(
        config=_make_config(), client=_mock_runpod_client(),
        output_dir=tmp_path / "out", http_client=http,
        poll_interval_sec=0.0,
    )
    results = await engine.swap_batch(src, [t1, t2], cancel_check=cancel_check)
    assert results[0] is not None
    assert results[1] is None  # never attempted


# ── error paths ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_swap_batch_missing_source_raises_value_error(tmp_path):
    bogus = tmp_path / "ghost.jpg"
    t1 = _make_image(tmp_path, "t1.jpg")
    engine = FaceSwapEngine(
        config=_make_config(), client=_mock_runpod_client(),
        output_dir=tmp_path / "out",
    )
    with pytest.raises(ValueError, match="image not found"):
        await engine.swap_batch(bogus, [t1])


@pytest.mark.anyio
async def test_swap_single_propagates_failure(tmp_path):
    """swap() is sugar over swap_batch(); a None result raises FaceSwapError."""
    src = _make_image(tmp_path, "src.jpg")
    t1 = _make_image(tmp_path, "t1.jpg")

    http = MagicMock(spec=httpx.AsyncClient)
    http.aclose = AsyncMock()
    http.get = AsyncMock(side_effect=[
        _json_response({"system": "ok"}),
        _json_response({"ReActorFaceSwap": {}}),
        # /history: error
        _json_response({"p1": {"status": {"completed": True, "status_str": "error", "messages": ["boom"]}}}),
    ])
    http.post = AsyncMock(side_effect=[
        _json_response({"name": "src.jpg"}),
        _json_response({"name": "t1.jpg"}),
        _json_response({"prompt_id": "p1"}),
    ])

    engine = FaceSwapEngine(
        config=_make_config(), client=_mock_runpod_client(),
        output_dir=tmp_path / "out", http_client=http,
        poll_interval_sec=0.0,
    )
    with pytest.raises(FaceSwapError, match="swap failed"):
        await engine.swap(src, t1)


@pytest.mark.anyio
async def test_swap_batch_node_errors_become_per_photo_failure(tmp_path):
    src = _make_image(tmp_path, "src.jpg")
    t1 = _make_image(tmp_path, "t1.jpg")

    http = MagicMock(spec=httpx.AsyncClient)
    http.aclose = AsyncMock()
    http.get = AsyncMock(side_effect=[
        _json_response({"system": "ok"}),
        _json_response({"ReActorFaceSwap": {}}),
    ])
    http.post = AsyncMock(side_effect=[
        _json_response({"name": "src.jpg"}),
        _json_response({"name": "t1.jpg"}),
        _json_response({"node_errors": {"3": "ReActor not available"}}),
    ])

    engine = FaceSwapEngine(
        config=_make_config(), client=_mock_runpod_client(),
        output_dir=tmp_path / "out", http_client=http,
        poll_interval_sec=0.0,
    )
    results = await engine.swap_batch(src, [t1])
    assert results == [None]  # captured, not raised


@pytest.mark.anyio
async def test_swap_batch_stops_pod_even_when_no_targets_succeed(tmp_path):
    src = _make_image(tmp_path, "src.jpg")
    t1 = _make_image(tmp_path, "t1.jpg")
    http = MagicMock(spec=httpx.AsyncClient)
    http.aclose = AsyncMock()
    http.get = AsyncMock(side_effect=[
        _json_response({"system": "ok"}),
        _json_response({"ReActorFaceSwap": {}}),
    ])
    http.post = AsyncMock(side_effect=[
        _json_response({"name": "src.jpg"}),
        _json_response({"name": "t1.jpg"}),
        _json_response({"node_errors": {"3": "boom"}}),
    ])
    client = _mock_runpod_client()
    engine = FaceSwapEngine(
        config=_make_config(), client=client,
        output_dir=tmp_path / "out", http_client=http,
        poll_interval_sec=0.0,
    )
    await engine.swap_batch(src, [t1])
    client.stop_pod.assert_awaited_once_with("pod_abc")


# ── workflow building ───────────────────────────────────────────────────────


def test_build_workflow_injects_source_and_target_filenames(tmp_path):
    engine = FaceSwapEngine(
        config=_make_config(), client=_mock_runpod_client(),
        output_dir=tmp_path / "out",
    )
    wf = engine._build_workflow("src.jpg", "tgt.jpg", "ReActorFaceSwap")
    assert wf["1"]["inputs"]["image"] == "src.jpg"
    assert wf["2"]["inputs"]["image"] == "tgt.jpg"
    assert wf["3"]["class_type"] == "ReActorFaceSwap"
    assert "_comment" not in wf


# ── explicit pod override (FACE_SWAP_POD_ID) ────────────────────────────────


@pytest.mark.anyio
async def test_find_or_start_pod_uses_explicit_pod_when_running(
    tmp_path, monkeypatch,
):
    """FACE_SWAP_POD_ID set + pod RUNNING → engine returns it directly."""
    monkeypatch.setenv("FACE_SWAP_POD_ID", "pod_explicit")

    explicit_pod = PodInfo.model_construct(
        id="pod_explicit", name="some-arbitrary-name",
        desired_status="RUNNING", cost_per_hr=1.59,
    )
    # Include a jarvis-m2-* candidate too; the explicit branch must win.
    prefix_pod = PodInfo.model_construct(
        id="pod_other", name="jarvis-m2-other",
        desired_status="RUNNING", cost_per_hr=1.59,
    )
    client = _mock_runpod_client(pods=[prefix_pod, explicit_pod])

    engine = FaceSwapEngine(
        config=_make_config(), client=client,
        output_dir=tmp_path / "out",
    )
    pod, pod_id, reused = await engine._find_or_start_pod(client)

    assert pod is explicit_pod  # same object, not a re-fetch
    assert pod_id == "pod_explicit"
    assert reused is True
    client.resume_pod.assert_not_awaited()
    client.start_pod.assert_not_awaited()
    client.wait_for_ready.assert_not_awaited()


@pytest.mark.anyio
async def test_find_or_start_pod_resumes_explicit_pod_when_stopped(
    tmp_path, monkeypatch,
):
    """FACE_SWAP_POD_ID set + pod STOPPED → engine resumes it."""
    monkeypatch.setenv("FACE_SWAP_POD_ID", "pod_explicit")

    stopped_pod = PodInfo.model_construct(
        id="pod_explicit", name="some-arbitrary-name",
        desired_status="STOPPED", cost_per_hr=1.59,
    )
    # Distinct objects: resume_pod() returns mid_pod, wait_for_ready() returns
    # ready_pod. Asserting `pod is ready_pod` proves the engine returns the
    # wait_for_ready result — a future implementer who drops the wait call
    # would return mid_pod, breaking the test.
    mid_pod = PodInfo.model_construct(
        id="pod_explicit", name="some-arbitrary-name",
        desired_status="STARTING", cost_per_hr=1.59,
    )
    ready_pod = PodInfo.model_construct(
        id="pod_explicit", name="some-arbitrary-name",
        desired_status="RUNNING", cost_per_hr=1.59,
    )
    client = _mock_runpod_client(pods=[stopped_pod])
    client.resume_pod = AsyncMock(return_value=mid_pod)
    client.wait_for_ready = AsyncMock(return_value=ready_pod)

    engine = FaceSwapEngine(
        config=_make_config(), client=client,
        output_dir=tmp_path / "out",
    )
    pod, pod_id, reused = await engine._find_or_start_pod(client)

    assert pod is ready_pod  # engine must return wait_for_ready's result, not resume_pod's
    assert pod_id == "pod_explicit"
    assert reused is False
    client.resume_pod.assert_awaited_once_with("pod_explicit")
    client.wait_for_ready.assert_awaited_once()
    client.start_pod.assert_not_awaited()


@pytest.mark.anyio
async def test_find_or_start_pod_falls_back_to_prefix_when_explicit_missing(
    tmp_path, monkeypatch, caplog,
):
    """FACE_SWAP_POD_ID set but id not in list_pods → fall back, warn."""
    monkeypatch.setenv("FACE_SWAP_POD_ID", "pod_does_not_exist")

    prefix_pod = PodInfo.model_construct(
        id="pod_prefix", name="jarvis-m2-other",
        desired_status="RUNNING", cost_per_hr=1.59,
    )
    client = _mock_runpod_client(pods=[prefix_pod])

    engine = FaceSwapEngine(
        config=_make_config(), client=client,
        output_dir=tmp_path / "out",
    )
    with caplog.at_level("WARNING"):
        pod, pod_id, reused = await engine._find_or_start_pod(client)

    assert pod is prefix_pod
    assert pod_id == "pod_prefix"  # prefix-search winner
    assert reused is True
    assert "pod_does_not_exist" in caplog.text
    assert "falling back to prefix search" in caplog.text
    client.resume_pod.assert_not_awaited()
    client.start_pod.assert_not_awaited()


@pytest.mark.anyio
async def test_find_or_start_pod_explicit_resume_api_error_raises_face_swap_error(
    tmp_path, monkeypatch,
):
    """FACE_SWAP_POD_ID set + resume_pod raises RunpodApiError → FaceSwapError.

    Real RunPod API failures during resume must NOT silently fall back to
    prefix search — they signal a real problem (quota, supply, network) that
    should surface to the caller.
    """
    monkeypatch.setenv("FACE_SWAP_POD_ID", "pod_explicit")

    stopped_pod = PodInfo.model_construct(
        id="pod_explicit", name="some-arbitrary-name",
        desired_status="STOPPED", cost_per_hr=1.59,
    )
    # Include a prefix pod to prove the engine does NOT fall back to it.
    prefix_pod = PodInfo.model_construct(
        id="pod_prefix", name="jarvis-m2-other",
        desired_status="RUNNING", cost_per_hr=1.59,
    )
    client = _mock_runpod_client(pods=[stopped_pod, prefix_pod])
    client.resume_pod = AsyncMock(side_effect=RunpodApiError("quota exceeded"))

    engine = FaceSwapEngine(
        config=_make_config(), client=client,
        output_dir=tmp_path / "out",
    )
    with pytest.raises(
        FaceSwapError,
        match="resume of explicit pod pod_explicit failed",
    ):
        await engine._find_or_start_pod(client)

    client.start_pod.assert_not_awaited()


# ── #44 collision fix + keep-pod-running flag ────────────────────────────────


@pytest.mark.anyio
async def test_swap_batch_workflow_uses_server_returned_name_when_renamed(
    tmp_path,
):
    """If ComfyUI echoes a renamed filename, the workflow must use the echo.

    Locks in the existing chain: _upload_image returns payload['name'] which
    flows through _swap_one into _build_workflow as the node-2 image input.
    A future regression that uses image_path.name in _build_workflow would
    break this test.
    """
    src = _make_image(tmp_path, "src.jpg")
    t1 = _make_image(tmp_path, "t1.jpg")

    submitted_workflow: dict = {}
    renamed_target = "deadbeefcafebabe1234567890abcdef_t1 (2).jpg"

    async def fake_post(url, *args, **kwargs):
        if "/upload/image" in url:
            files = kwargs.get("files") or {}
            # Source upload echoes its own name; target upload returns the
            # renamed name to simulate a server-side collision rename.
            name_field = files.get("image", (None,))[0] or ""
            if "src" in name_field:
                return _json_response({"name": "src_echo.jpg"})
            return _json_response({"name": renamed_target})
        if "/prompt" in url:
            submitted_workflow.update(
                kwargs.get("json", {}).get("prompt", {})
            )
            return _json_response({"prompt_id": "p1"})
        return _json_response({})

    http = MagicMock(spec=httpx.AsyncClient)
    http.aclose = AsyncMock()
    http.post = AsyncMock(side_effect=fake_post)
    http.get = AsyncMock(side_effect=[
        _json_response({"system": "ok"}),
        _json_response({"ReActorFaceSwap": {}}),
        _json_response(_success_history("p1", "swap_out.png")),
        _bytes_response(b"\x89PNG" + b"\x00" * 20_000),
    ])

    engine = FaceSwapEngine(
        config=_make_config(), client=_mock_runpod_client(),
        output_dir=tmp_path / "out", http_client=http,
        poll_interval_sec=0.0,
    )
    results = await engine.swap_batch(src, [t1])
    assert results[0] is not None
    assert submitted_workflow["2"]["inputs"]["image"] == renamed_target
    assert submitted_workflow["1"]["inputs"]["image"] == "src_echo.jpg"
