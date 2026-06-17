# -*- coding: utf-8 -*-
"""Tests for :class:`VideoFaceSwapEngine` and its planning helpers.

All RunPod + ComfyUI traffic is mocked (no pod, no money). Video *decoding*
(``_probe_video`` / cv2) is the other external boundary; the async swap tests
stub it the same way they stub httpx, so the orchestration is exercised without
a real video file. The pure planners (limits, cost, workflow build) run for real.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import SecretStr

from app.services.block_m2_face_swap.face_swap_engine import FaceSwapError
from app.services.block_m2_face_swap.video_face_swap_engine import (
    VideoFaceSwapEngine,
    VideoMeta,
    VideoTooLongError,
    estimate_video_swap,
    plan_video_swap,
)
from app.services.block_m2_video.runpod.runpod_client import PodInfo
from app.services.block_m2_video.runpod.runpod_config import RunpodConfig


# ── helpers (mirror test_swapbatch_face_swap_engine harness) ─────────────────


def _make_config() -> RunpodConfig:
    return RunpodConfig.model_construct(
        api_key=SecretStr("rpa_test_xxxx"), api_endpoint="https://x/graphql",
        network_volume_id="vol", datacenter="EU-TEST-1",
        gpu_type_id="GPU-A", gpu_fallback_id="GPU-B", gpu_count=1,
        template_id=None, docker_image="img:latest", comfyui_port=8188,
        comfyui_auth_token=None, max_budget_usd_per_day=8.0,
        max_pod_lifetime_min=120, guardian_check_interval_sec=30,
        emergency_stop_enabled=True,
    )


def _make_file(tmp_path: Path, name: str, blob: bytes = b"\x00" * 1024) -> Path:
    p = tmp_path / name
    p.write_bytes(blob)
    return p


def _json_response(payload: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status, content=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        request=httpx.Request("GET", "http://t/"),
    )


def _bytes_response(content: bytes, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status, content=content,
        request=httpx.Request("GET", "http://t/"),
    )


def _mock_client() -> MagicMock:
    pod = PodInfo.model_construct(
        id="pod_abc", name="jarvis-m2-test",
        desired_status="RUNNING", cost_per_hr=1.59,
    )
    c = MagicMock()
    c.list_pods = AsyncMock(return_value=[pod])
    c.start_pod = AsyncMock(return_value=pod)
    c.resume_pod = AsyncMock(return_value=pod)
    c.wait_for_ready = AsyncMock(return_value=pod)
    c.get_pod_public_url = AsyncMock(return_value="http://t:8188")
    c.stop_pod = AsyncMock(return_value=True)
    c.aclose = AsyncMock()
    return c


def _happy_http() -> MagicMock:
    """An httpx mock that drives swap_video down its happy path once."""
    http = MagicMock(spec=httpx.AsyncClient)
    http.aclose = AsyncMock()
    http.get = AsyncMock(side_effect=[
        _json_response({"system": "ok"}),            # system_stats
        _json_response({"ReActorFaceSwap": {}}),     # object_info
        _json_response(_video_history("p1", "v.mp4")),
        _bytes_response(b"\x00" * 200_000),          # /view mp4
    ])
    http.post = AsyncMock(side_effect=[
        _json_response({"name": "face.jpg"}),        # upload source
        _json_response({"name": "clip.mp4"}),        # upload video
        _json_response({"prompt_id": "p1"}),         # /prompt
    ])
    return http


def _submitted_workflow(http: MagicMock) -> dict:
    """Extract the workflow graph POSTed to /prompt from an http mock."""
    calls = [c for c in http.post.call_args_list if c.args[0].endswith("/prompt")]
    assert calls, "no /prompt POST was made"
    return calls[0].kwargs["json"]["prompt"]


def _video_history(prompt_id: str, filename: str) -> dict:
    # VHS_VideoCombine emits under "gifs" even for mp4; extractor scans by ext.
    return {
        prompt_id: {
            "status": {"completed": True, "status_str": "success"},
            "outputs": {"4": {"gifs": [
                {"filename": filename, "subfolder": "", "type": "output"}
            ]}},
        }
    }


# ── plan_video_swap (pure) ───────────────────────────────────────────────────


def test_plan_rejects_video_longer_than_limit():
    meta = VideoMeta(fps=30.0, frame_count=30 * 90, width=1280, height=720)  # 90s
    with pytest.raises(VideoTooLongError):
        plan_video_swap(meta, max_seconds=60.0, max_height=1080)


def test_plan_accepts_video_within_limit():
    meta = VideoMeta(fps=24.0, frame_count=24 * 30, width=1280, height=720)  # 30s
    plan = plan_video_swap(meta, max_seconds=60.0, max_height=1080)
    assert plan.downscale_to is None  # under 1080p, no resize


def test_plan_downscales_when_taller_than_max_height():
    meta = VideoMeta(fps=24.0, frame_count=240, width=3840, height=2160)  # 4K, 10s
    plan = plan_video_swap(meta, max_seconds=60.0, max_height=1080)
    assert plan.downscale_to == (1920, 1080)  # half-scale, aspect kept, even dims


def test_plan_downscale_dims_are_even():
    meta = VideoMeta(fps=24.0, frame_count=240, width=1080, height=1920)  # vertical
    plan = plan_video_swap(meta, max_seconds=60.0, max_height=1080)
    w, h = plan.downscale_to
    assert h == 1080 and w % 2 == 0 and h % 2 == 0


def test_plan_frame_load_cap_is_hard_ceiling_from_max_seconds():
    meta = VideoMeta(fps=24.0, frame_count=240, width=640, height=480)
    plan = plan_video_swap(meta, max_seconds=60.0, max_height=1080)
    assert plan.frame_load_cap == math.ceil(60.0 * 24.0)


# ── estimate_video_swap (pure) ───────────────────────────────────────────────


def test_estimate_is_positive_and_scales_with_frames():
    usd_a, min_a = estimate_video_swap(240, 24.0)
    usd_b, min_b = estimate_video_swap(720, 24.0)
    assert usd_a > 0 and min_a > 0
    assert usd_b > usd_a and min_b > min_a


# ── occlusion cost slowdown (Stage 4) ────────────────────────────────────────
# Occlusion runs SAM (segmentation) per frame on top of the swap, so the
# per-frame time grows ~1.5-2x. The estimate must reflect that when occlusion
# is requested, so the budget guard / user quote isn't an under-estimate.


def test_estimate_occlusion_raises_time_by_default_factor(monkeypatch):
    monkeypatch.delenv("VIDEO_SWAP_SEC_PER_FRAME", raising=False)
    monkeypatch.delenv("VIDEO_SWAP_OCCLUSION_SLOWDOWN", raising=False)
    _, min_base = estimate_video_swap(1440, 24.0)
    _, min_occ = estimate_video_swap(1440, 24.0, occlusion=True)
    assert min_occ == round(min_base * 1.8, 1)  # default slowdown 1.8x


def test_estimate_occlusion_slowdown_env_override(monkeypatch):
    monkeypatch.delenv("VIDEO_SWAP_SEC_PER_FRAME", raising=False)
    monkeypatch.setenv("VIDEO_SWAP_OCCLUSION_SLOWDOWN", "2.5")
    _, min_base = estimate_video_swap(1440, 24.0)
    _, min_occ = estimate_video_swap(1440, 24.0, occlusion=True)
    assert min_occ == round(min_base * 2.5, 1)


def test_estimate_no_slowdown_when_occlusion_false(monkeypatch):
    monkeypatch.setenv("VIDEO_SWAP_OCCLUSION_SLOWDOWN", "2.5")
    base = estimate_video_swap(1440, 24.0)
    assert estimate_video_swap(1440, 24.0, occlusion=False) == base


def test_plan_passes_occlusion_to_estimate():
    meta = VideoMeta(fps=24.0, frame_count=1440, width=640, height=480)
    plan_off = plan_video_swap(meta, max_seconds=60.0, max_height=1080)
    plan_on = plan_video_swap(meta, max_seconds=60.0, max_height=1080, occlusion=True)
    assert plan_on.est_minutes > plan_off.est_minutes
    assert plan_on.est_usd > plan_off.est_usd


# ── _build_video_workflow ────────────────────────────────────────────────────


def test_build_workflow_injects_filenames_fps_and_cap(tmp_path):
    engine = VideoFaceSwapEngine(config=_make_config(), client=_mock_client(),
                                 output_dir=tmp_path / "out")
    meta = VideoMeta(fps=24.0, frame_count=240, width=640, height=480)
    plan = plan_video_swap(meta, max_seconds=60.0, max_height=1080)
    wf = engine._build_video_workflow(
        source_filename="face.jpg", video_filename="clip.mp4",
        plan=plan, reactor_class="ReActorFaceSwap",
    )
    assert wf["1"]["inputs"]["video"] == "clip.mp4"
    assert wf["1"]["inputs"]["frame_load_cap"] == plan.frame_load_cap
    assert wf["2"]["inputs"]["image"] == "face.jpg"
    assert wf["3"]["class_type"] == "ReActorFaceSwap"
    assert wf["4"]["inputs"]["frame_rate"] == 24
    assert "_comment" not in wf


def test_build_workflow_sets_downscale_dims_when_planned(tmp_path):
    engine = VideoFaceSwapEngine(config=_make_config(), client=_mock_client(),
                                 output_dir=tmp_path / "out")
    meta = VideoMeta(fps=24.0, frame_count=240, width=3840, height=2160)
    plan = plan_video_swap(meta, max_seconds=60.0, max_height=1080)
    wf = engine._build_video_workflow(
        source_filename="f.jpg", video_filename="c.mp4",
        plan=plan, reactor_class="ReActorFaceSwap",
    )
    assert wf["1"]["inputs"]["custom_width"] == 1920
    assert wf["1"]["inputs"]["custom_height"] == 1080


# ── tunable ReActor / resolution knobs (env-overridable) ─────────────────────


def test_build_workflow_sets_default_face_restore_visibility(tmp_path, monkeypatch):
    monkeypatch.delenv("VIDEO_SWAP_FACE_RESTORE_VISIBILITY", raising=False)
    engine = VideoFaceSwapEngine(config=_make_config(), client=_mock_client(),
                                 output_dir=tmp_path / "out")
    meta = VideoMeta(fps=24.0, frame_count=240, width=640, height=480)
    plan = plan_video_swap(meta, max_seconds=60.0, max_height=1080)
    wf = engine._build_video_workflow(
        source_filename="f.jpg", video_filename="c.mp4",
        plan=plan, reactor_class="ReActorFaceSwap",
    )
    assert wf["3"]["inputs"]["face_restore_visibility"] == 0.7


def test_build_workflow_honors_face_restore_visibility_env(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_SWAP_FACE_RESTORE_VISIBILITY", "0.85")
    engine = VideoFaceSwapEngine(config=_make_config(), client=_mock_client(),
                                 output_dir=tmp_path / "out")
    meta = VideoMeta(fps=24.0, frame_count=240, width=640, height=480)
    plan = plan_video_swap(meta, max_seconds=60.0, max_height=1080)
    wf = engine._build_video_workflow(
        source_filename="f.jpg", video_filename="c.mp4",
        plan=plan, reactor_class="ReActorFaceSwap",
    )
    assert wf["3"]["inputs"]["face_restore_visibility"] == 0.85


@pytest.mark.anyio
async def test_swap_video_defaults_max_height_to_1080(tmp_path, monkeypatch):
    monkeypatch.delenv("VIDEO_SWAP_MAX_HEIGHT", raising=False)
    face = _make_file(tmp_path, "face.jpg")
    clip = _make_file(tmp_path, "clip.mp4")
    http = _happy_http()
    engine = _engine_with_http(http, tmp_path)
    monkeypatch.setattr(engine, "_probe_video", lambda p: VideoMeta(
        fps=24.0, frame_count=240, width=1000, height=1500))
    await engine.swap_video(face, clip)
    assert _submitted_workflow(http)["1"]["inputs"]["custom_height"] == 1080


@pytest.mark.anyio
async def test_swap_video_max_height_env_controls_downscale(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_SWAP_MAX_HEIGHT", "720")
    face = _make_file(tmp_path, "face.jpg")
    clip = _make_file(tmp_path, "clip.mp4")
    http = _happy_http()
    engine = _engine_with_http(http, tmp_path)
    monkeypatch.setattr(engine, "_probe_video", lambda p: VideoMeta(
        fps=24.0, frame_count=240, width=1000, height=1500))
    await engine.swap_video(face, clip)
    assert _submitted_workflow(http)["1"]["inputs"]["custom_height"] == 720


# ── swap_video orchestration (mocked pod + comfy) ────────────────────────────


def _engine_with_http(http, tmp_path, **kw):
    return VideoFaceSwapEngine(
        config=_make_config(), client=_mock_client(),
        output_dir=tmp_path / "out", http_client=http,
        poll_interval_sec=0.0, **kw,
    )


@pytest.mark.anyio
async def test_swap_video_happy_path_returns_mp4(tmp_path, monkeypatch):
    face = _make_file(tmp_path, "face.jpg")
    clip = _make_file(tmp_path, "clip.mp4")

    http = MagicMock(spec=httpx.AsyncClient)
    http.aclose = AsyncMock()
    http.get = AsyncMock(side_effect=[
        _json_response({"system": "ok"}),                       # system_stats
        _json_response({"ReActorFaceSwap": {}}),                # object_info
        _json_response(_video_history("p1", "video_swap_00001.mp4")),
        _bytes_response(b"\x00" * 200_000),                     # /view mp4
    ])
    http.post = AsyncMock(side_effect=[
        _json_response({"name": "face.jpg"}),                   # upload source
        _json_response({"name": "clip.mp4"}),                   # upload video
        _json_response({"prompt_id": "p1"}),                    # /prompt
    ])

    engine = _engine_with_http(http, tmp_path)
    monkeypatch.setattr(engine, "_probe_video", lambda p: VideoMeta(
        fps=24.0, frame_count=240, width=640, height=480))

    out = await engine.swap_video(face, clip)
    assert out.exists() and out.suffix == ".mp4"
    uploads = [c for c in http.post.call_args_list if "/upload/image" in c.args[0]]
    assert len(uploads) == 2  # source + video


@pytest.mark.anyio
async def test_swap_video_rejects_too_long_before_touching_pod(tmp_path, monkeypatch):
    face = _make_file(tmp_path, "face.jpg")
    clip = _make_file(tmp_path, "clip.mp4")
    client = _mock_client()
    engine = VideoFaceSwapEngine(config=_make_config(), client=client,
                                 output_dir=tmp_path / "out")
    monkeypatch.setattr(engine, "_probe_video", lambda p: VideoMeta(
        fps=30.0, frame_count=30 * 120, width=1280, height=720))  # 120s

    with pytest.raises(VideoTooLongError):
        await engine.swap_video(face, clip, max_seconds=60.0)
    client.list_pods.assert_not_awaited()  # never spent a pod


@pytest.mark.anyio
async def test_swap_video_missing_video_raises_value_error(tmp_path):
    face = _make_file(tmp_path, "face.jpg")
    engine = VideoFaceSwapEngine(config=_make_config(), client=_mock_client(),
                                 output_dir=tmp_path / "out")
    with pytest.raises(ValueError, match="not found"):
        await engine.swap_video(face, tmp_path / "ghost.mp4")


@pytest.mark.anyio
async def test_swap_video_stops_pod_when_keep_flag_unset(tmp_path, monkeypatch):
    face = _make_file(tmp_path, "face.jpg")
    clip = _make_file(tmp_path, "clip.mp4")
    monkeypatch.delenv("FACE_SWAP_KEEP_POD_RUNNING", raising=False)

    http = MagicMock(spec=httpx.AsyncClient)
    http.aclose = AsyncMock()
    http.get = AsyncMock(side_effect=[
        _json_response({"system": "ok"}),
        _json_response({"ReActorFaceSwap": {}}),
        _json_response(_video_history("p1", "v.mp4")),
        _bytes_response(b"\x00" * 200_000),
    ])
    http.post = AsyncMock(side_effect=[
        _json_response({"name": "face.jpg"}),
        _json_response({"name": "clip.mp4"}),
        _json_response({"prompt_id": "p1"}),
    ])
    client = _mock_client()
    engine = VideoFaceSwapEngine(
        config=_make_config(), client=client, output_dir=tmp_path / "out",
        http_client=http, poll_interval_sec=0.0,
    )
    monkeypatch.setattr(engine, "_probe_video", lambda p: VideoMeta(
        fps=24.0, frame_count=240, width=640, height=480))
    await engine.swap_video(face, clip)
    client.stop_pod.assert_awaited_once_with("pod_abc")


# ── occlusion mask helper probe (Stage 2) ────────────────────────────────────
# The ReActorMaskHelper node restores occluders (a hand/food in front of the
# face) that the raw swap would paint over. It is optional: the engine probes
# /object_info for the class and only wires it when present, so a pod whose
# ReActor pack lacks the node degrades to the plain swap instead of submitting
# a graph that references a missing class.


@pytest.mark.anyio
async def test_probe_mask_helper_true_when_node_present(tmp_path):
    http = MagicMock(spec=httpx.AsyncClient)
    http.aclose = AsyncMock()
    http.get = AsyncMock(return_value=_json_response(
        {"ReActorFaceSwap": {}, "ReActorMaskHelper": {}}))
    engine = _engine_with_http(http, tmp_path)
    assert await engine._probe_mask_helper_available("http://t:8188") is True
    http.get.assert_awaited_once_with("http://t:8188/object_info")


@pytest.mark.anyio
async def test_probe_mask_helper_false_when_node_absent(tmp_path):
    http = MagicMock(spec=httpx.AsyncClient)
    http.aclose = AsyncMock()
    http.get = AsyncMock(return_value=_json_response({"ReActorFaceSwap": {}}))
    engine = _engine_with_http(http, tmp_path)
    assert await engine._probe_mask_helper_available("http://t:8188") is False


@pytest.mark.anyio
async def test_probe_mask_helper_false_on_http_error(tmp_path):
    http = MagicMock(spec=httpx.AsyncClient)
    http.aclose = AsyncMock()
    http.get = AsyncMock(side_effect=httpx.ConnectError("boom"))
    engine = _engine_with_http(http, tmp_path)
    # a probe failure must NOT raise — it degrades to "no mask helper"
    assert await engine._probe_mask_helper_available("http://t:8188") is False


@pytest.mark.anyio
async def test_probe_mask_helper_false_on_non_200(tmp_path):
    http = MagicMock(spec=httpx.AsyncClient)
    http.aclose = AsyncMock()
    http.get = AsyncMock(return_value=_json_response({}, status=500))
    engine = _engine_with_http(http, tmp_path)
    assert await engine._probe_mask_helper_available("http://t:8188") is False


@pytest.mark.anyio
async def test_probe_mask_helper_false_on_bad_json(tmp_path):
    http = MagicMock(spec=httpx.AsyncClient)
    http.aclose = AsyncMock()
    http.get = AsyncMock(return_value=_bytes_response(b"not json", status=200))
    engine = _engine_with_http(http, tmp_path)
    assert await engine._probe_mask_helper_available("http://t:8188") is False


# ── occlusion graph wiring under VIDEO_SWAP_OCCLUSION_MASK (Stage 3) ──────────
# When the flag is on AND the node is available, a ReActorMaskHelper node (id
# "5") is spliced between ReActorFaceSwap (3) and VHS_VideoCombine (4): it takes
# the original frames (1,0) + the swapped frames (3,0) and its corrected output
# (5,0) feeds the combiner. Default OFF → graph is byte-for-byte the old one.


def _plan(tmp_unused=None):
    meta = VideoMeta(fps=24.0, frame_count=240, width=640, height=480)
    return plan_video_swap(meta, max_seconds=60.0, max_height=1080)


def _build(engine, *, mask_helper_available):
    return engine._build_video_workflow(
        source_filename="face.jpg", video_filename="clip.mp4",
        plan=_plan(), reactor_class="ReActorFaceSwap",
        mask_helper_available=mask_helper_available,
    )


def test_build_workflow_no_mask_node_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.delenv("VIDEO_SWAP_OCCLUSION_MASK", raising=False)
    engine = VideoFaceSwapEngine(config=_make_config(), client=_mock_client(),
                                 output_dir=tmp_path / "out")
    wf = _build(engine, mask_helper_available=True)  # available but flag OFF
    assert "5" not in wf
    # byte-for-byte original: no normalizer node, swap reads raw frames
    assert "6" not in wf
    assert wf["3"]["inputs"]["input_image"] == ["1", 0]
    assert wf["4"]["inputs"]["images"] == ["3", 0]


def test_build_workflow_no_mask_node_when_helper_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_SWAP_OCCLUSION_MASK", "1")
    engine = VideoFaceSwapEngine(config=_make_config(), client=_mock_client(),
                                 output_dir=tmp_path / "out")
    wf = _build(engine, mask_helper_available=False)  # flag ON but node missing
    assert "5" not in wf
    # byte-for-byte original: no normalizer node, swap reads raw frames
    assert "6" not in wf
    assert wf["3"]["inputs"]["input_image"] == ["1", 0]
    assert wf["4"]["inputs"]["images"] == ["3", 0]


def test_build_workflow_inserts_mask_node_when_enabled_and_available(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_SWAP_OCCLUSION_MASK", "1")
    engine = VideoFaceSwapEngine(config=_make_config(), client=_mock_client(),
                                 output_dir=tmp_path / "out")
    wf = _build(engine, mask_helper_available=True)
    assert wf["5"]["class_type"] == "ReActorMaskHelper"
    # node "6" normalizes RGBA->RGB (B-53) off the raw frames (1,0)
    assert wf["6"]["class_type"] == "SplitImageWithAlpha"
    assert wf["6"]["inputs"]["image"] == ["1", 0]
    # both consumers read the normalized RGB source (node 6), not raw frames
    assert wf["3"]["inputs"]["input_image"] == ["6", 0]
    assert wf["5"]["inputs"]["image"] == ["6", 0]
    # the mask helper still consumes the (now-RGB) swap output
    assert wf["5"]["inputs"]["swapped_image"] == ["3", 0]
    # the combiner now consumes the corrected output, not the raw swap
    assert wf["4"]["inputs"]["images"] == ["5", 0]
    # audio still comes straight from the VHS loader (no regression)
    assert wf["4"]["inputs"]["audio"] == ["1", 2]


def test_build_mask_helper_node_defaults_image_to_loader():
    # Guards the fallback branch (no image_src) that production no longer
    # exercises: it must preserve the original VHS loader wiring ["1", 0].
    assert VideoFaceSwapEngine._build_mask_helper_node()["inputs"]["image"] == ["1", 0]


def test_build_workflow_mask_node_has_face_bbox_and_sam_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_SWAP_OCCLUSION_MASK", "1")
    monkeypatch.delenv("VIDEO_SWAP_OCCLUSION_BBOX_MODEL", raising=False)
    monkeypatch.delenv("VIDEO_SWAP_OCCLUSION_SAM_MODEL", raising=False)
    engine = VideoFaceSwapEngine(config=_make_config(), client=_mock_client(),
                                 output_dir=tmp_path / "out")
    ins = _build(engine, mask_helper_available=True)["5"]["inputs"]
    # face-trained YOLO (NOT generic yolov8m) + lightest SAM; the bbox value
    # carries the Impact-Pack ``bbox/`` subfolder prefix ComfyUI's dropdown
    # requires (a bare ``face_yolov8m.pt`` fails /prompt with value_not_in_list)
    assert ins["bbox_model_name"] == "bbox/face_yolov8m.pt"
    assert ins["sam_model_name"] == "sam_vit_b_01ec64.pth"
    # required inputs present with schema defaults
    assert ins["sam_threshold"] == 0.93
    assert ins["bbox_threshold"] == 0.5


def test_build_workflow_mask_node_honors_env_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_SWAP_OCCLUSION_MASK", "true")
    monkeypatch.setenv("VIDEO_SWAP_OCCLUSION_BBOX_MODEL", "face_yolov8n.pt")
    monkeypatch.setenv("VIDEO_SWAP_OCCLUSION_SAM_MODEL", "sam_vit_h_4b8939.pth")
    monkeypatch.setenv("VIDEO_SWAP_OCCLUSION_SAM_THRESHOLD", "0.8")
    engine = VideoFaceSwapEngine(config=_make_config(), client=_mock_client(),
                                 output_dir=tmp_path / "out")
    ins = _build(engine, mask_helper_available=True)["5"]["inputs"]
    assert ins["bbox_model_name"] == "face_yolov8n.pt"
    assert ins["sam_model_name"] == "sam_vit_h_4b8939.pth"
    assert ins["sam_threshold"] == 0.8


@pytest.mark.anyio
async def test_swap_video_wires_mask_helper_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_SWAP_OCCLUSION_MASK", "1")
    face = _make_file(tmp_path, "face.jpg")
    clip = _make_file(tmp_path, "clip.mp4")
    http = MagicMock(spec=httpx.AsyncClient)
    http.aclose = AsyncMock()
    http.get = AsyncMock(side_effect=[
        _json_response({"system": "ok"}),                                  # system_stats
        _json_response({"ReActorFaceSwap": {}, "ReActorMaskHelper": {}}),  # reactor probe
        _json_response({"ReActorFaceSwap": {}, "ReActorMaskHelper": {}}),  # mask probe
        _json_response(_video_history("p1", "v.mp4")),
        _bytes_response(b"\x00" * 200_000),
    ])
    http.post = AsyncMock(side_effect=[
        _json_response({"name": "face.jpg"}),
        _json_response({"name": "clip.mp4"}),
        _json_response({"prompt_id": "p1"}),
    ])
    engine = _engine_with_http(http, tmp_path)
    monkeypatch.setattr(engine, "_probe_video", lambda p: VideoMeta(
        fps=24.0, frame_count=240, width=640, height=480))
    await engine.swap_video(face, clip)
    wf = _submitted_workflow(http)
    assert wf["5"]["class_type"] == "ReActorMaskHelper"
    assert wf["4"]["inputs"]["images"] == ["5", 0]


@pytest.mark.anyio
async def test_swap_video_plan_estimate_reflects_occlusion_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_SWAP_OCCLUSION_MASK", "1")
    monkeypatch.delenv("VIDEO_SWAP_SEC_PER_FRAME", raising=False)
    monkeypatch.delenv("VIDEO_SWAP_OCCLUSION_SLOWDOWN", raising=False)
    face = _make_file(tmp_path, "face.jpg")
    clip = _make_file(tmp_path, "clip.mp4")
    http = MagicMock(spec=httpx.AsyncClient)
    http.aclose = AsyncMock()
    http.get = AsyncMock(side_effect=[
        _json_response({"system": "ok"}),                                  # system_stats
        _json_response({"ReActorFaceSwap": {}, "ReActorMaskHelper": {}}),  # reactor probe
        _json_response({"ReActorFaceSwap": {}, "ReActorMaskHelper": {}}),  # mask probe
        _json_response(_video_history("p1", "v.mp4")),
        _bytes_response(b"\x00" * 200_000),
    ])
    http.post = AsyncMock(side_effect=[
        _json_response({"name": "face.jpg"}),
        _json_response({"name": "clip.mp4"}),
        _json_response({"prompt_id": "p1"}),
    ])
    engine = _engine_with_http(http, tmp_path)
    monkeypatch.setattr(engine, "_probe_video", lambda p: VideoMeta(
        fps=24.0, frame_count=240, width=640, height=480))
    events: list = []
    await engine.swap_video(face, clip, progress_cb=lambda s, p: events.append((s, p)))
    planned = next(p for s, p in events if s == "planned")
    # 240 frames * 0.5s * 1.8 / 60 = 3.6 min (vs 2.0 without occlusion)
    assert planned["est_minutes"] == 3.6
