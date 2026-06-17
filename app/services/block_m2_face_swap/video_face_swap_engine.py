# -*- coding: utf-8 -*-
"""Block M.2.6 video face-swap engine — ReActor-on-video via RunPod ComfyUI.

A thin specialisation of :class:`FaceSwapEngine` that swaps a face through an
**entire video** instead of a still: ``VHS_LoadVideo -> ReActorFaceSwap ->
VHS_VideoCombine`` (see ``workflows/video_face_swap.json``). It reuses the
parent's pod lifecycle, ComfyUI health/probe, upload, submit and poll plumbing
verbatim; only the workflow, the video probing/limits, the longer poll timeout
(video takes minutes, not seconds) and the mp4 download differ.

Safety rails (cheap to run, expensive to skip):

- **Length limit** (``max_seconds``, default 60) — a too-long clip is rejected
  *before* any pod is touched, so a fat-fingered request can't burn money.
- **Resolution cap** (``max_height``, default 1080) — taller videos are
  downscaled in-graph (VHS ``custom_width``/``custom_height``) to bound VRAM.
- **frame_load_cap** — a hard per-graph ceiling on frames loaded (belt &
  suspenders vs the length check) so a mis-probed stream can't OOM the pod.

Pod-side execution (real inswapper on frames) is verified manually on a live
pod; CI mocks the ComfyUI boundary exactly like the still-image engine.
"""
from __future__ import annotations

import copy
import json
import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.services.block_m2_face_swap.face_swap_engine import (
    _KEEP_POD_RUNNING_ENV,
    FaceSwapEngine,
    FaceSwapError,
)

logger = logging.getLogger(__name__)

_VIDEO_WORKFLOW_FILE = (
    Path(__file__).resolve().parent / "workflows" / "video_face_swap.json"
)
_VALID_VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
_VIDEO_POLL_INTERVAL_SEC = 15.0
_VIDEO_POLL_TIMEOUT_SEC = 1800  # 30 min — per-frame swap of a clip takes minutes
_MIN_VIDEO_OUTPUT_BYTES = 100 * 1024
_DEFAULT_VIDEO_OUTPUT_DIR = Path(r"C:\jarvis\data\block_m2_face_swap\video_outputs")

DEFAULT_MAX_SECONDS = 60.0
DEFAULT_MAX_HEIGHT = 1080
# ReActor's occlusion node: detects the face (bbox) + segments occluders (SAM)
# and restores foreground objects (a hand/food in front of the face) that the
# raw swap would paint over. Optional — only wired when present on the pod.
_MASK_HELPER_CLASS = "ReActorMaskHelper"
# GFPGAN at full visibility (1.0) re-renders the face independently per frame,
# which reads as shimmer/"redrawing" on video. 0.7 blends the raw swap back in
# for steadier temporal output; override with VIDEO_SWAP_FACE_RESTORE_VISIBILITY.
DEFAULT_FACE_RESTORE_VISIBILITY = 0.7


def _envf(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _envi(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _envs(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return raw.strip() if raw and raw.strip() else default


def _occlusion_enabled() -> bool:
    """True when the occlusion mask (ReActorMaskHelper) is opt-in via env.

    OFF by default: without the flag the video graph is unchanged, so the
    feature is dark until both the flag is set and the node+models are present.
    """
    return os.environ.get("VIDEO_SWAP_OCCLUSION_MASK", "").strip().lower() in {"1", "true"}


class VideoTooLongError(ValueError):
    """Raised when a target video exceeds the allowed length (budget guard)."""


@dataclass(frozen=True)
class VideoMeta:
    """Probed properties of a target video."""

    fps: float
    frame_count: int
    width: int
    height: int

    @property
    def duration_sec(self) -> float:
        return self.frame_count / self.fps if self.fps > 0 else 0.0


@dataclass(frozen=True)
class VideoSwapPlan:
    """A validated, cost-estimated plan for one video swap."""

    fps: float
    frame_count: int
    frame_load_cap: int
    downscale_to: tuple[int, int] | None
    est_usd: float
    est_minutes: float


def estimate_video_swap(
    frame_count: int, fps: float, *, occlusion: bool = False
) -> tuple[float, float]:
    """Return ``(usd, minutes)`` for per-frame ReActor swap of ``frame_count``.

    Grounded in the existing cost model (A100 ≈ $1.35/hr; the animate rate of
    720s = $0.27 implies the same). All knobs are env-overridable so the first
    real run can recalibrate without code changes.

    When ``occlusion`` is set, the per-frame time is multiplied by
    ``VIDEO_SWAP_OCCLUSION_SLOWDOWN`` (default 1.8) because the mask helper runs
    SAM segmentation on every frame on top of the swap — so the quote stays an
    over-estimate, not an under-estimate, if occlusion is enabled.
    """
    sec_per_frame = _envf("VIDEO_SWAP_SEC_PER_FRAME", 0.5)
    if occlusion:
        sec_per_frame *= _envf("VIDEO_SWAP_OCCLUSION_SLOWDOWN", 1.8)
    usd_per_hr = _envf("VIDEO_SWAP_USD_PER_HR", 1.35)
    cold_start_usd = _envf("VIDEO_SWAP_COLD_START_USD", 0.05)
    minutes = frame_count * sec_per_frame / 60.0
    usd = (minutes / 60.0) * usd_per_hr + cold_start_usd
    return round(usd, 2), round(minutes, 1)


def plan_video_swap(
    meta: VideoMeta,
    *,
    max_seconds: float = DEFAULT_MAX_SECONDS,
    max_height: int = DEFAULT_MAX_HEIGHT,
    occlusion: bool = False,
) -> VideoSwapPlan:
    """Validate ``meta`` against the safety rails and return a swap plan.

    Raises :class:`VideoTooLongError` if the clip is longer than ``max_seconds``.
    ``occlusion`` is threaded into the cost estimate (SAM-per-frame slowdown).
    """
    if meta.fps <= 0 or meta.frame_count <= 0:
        raise FaceSwapError(f"unusable video metadata: {meta}")
    if meta.duration_sec > max_seconds:
        raise VideoTooLongError(
            f"video is {meta.duration_sec:.1f}s; limit is {max_seconds:.0f}s"
        )

    downscale_to: tuple[int, int] | None = None
    if meta.height > max_height:
        scale = max_height / meta.height
        new_h = max_height
        new_w = int(round(meta.width * scale))
        new_w -= new_w % 2  # h264/yuv420p needs even dimensions
        new_h -= new_h % 2
        downscale_to = (new_w, new_h)

    frame_load_cap = math.ceil(max_seconds * meta.fps)
    est_usd, est_minutes = estimate_video_swap(
        meta.frame_count, meta.fps, occlusion=occlusion
    )
    return VideoSwapPlan(
        fps=meta.fps,
        frame_count=meta.frame_count,
        frame_load_cap=frame_load_cap,
        downscale_to=downscale_to,
        est_usd=est_usd,
        est_minutes=est_minutes,
    )


class VideoFaceSwapEngine(FaceSwapEngine):
    """ReActor face-swap over a whole video on RunPod ComfyUI."""

    engine_name = "runpod_reactor_video"

    def __init__(
        self,
        config=None,
        client=None,
        *,
        output_dir: Path | None = None,
        http_client=None,
        poll_interval_sec: float = _VIDEO_POLL_INTERVAL_SEC,
        poll_timeout_sec: int = _VIDEO_POLL_TIMEOUT_SEC,
        pod_ready_timeout_sec: int = 600,
    ) -> None:
        super().__init__(
            config,
            client,
            output_dir=output_dir or _DEFAULT_VIDEO_OUTPUT_DIR,
            http_client=http_client,
            poll_interval_sec=poll_interval_sec,
            poll_timeout_sec=poll_timeout_sec,
            pod_ready_timeout_sec=pod_ready_timeout_sec,
        )

    # ── public API ──────────────────────────────────────────────────────────

    async def swap_video(
        self,
        source_image: Path,
        target_video: Path,
        *,
        progress_cb=None,
        max_seconds: float = DEFAULT_MAX_SECONDS,
        max_height: int | None = None,
    ) -> Path:
        """Swap ``source_image``'s face into every frame of ``target_video``.

        Returns the path to the downloaded mp4. Rejects over-long videos before
        touching a pod. Pod lifecycle mirrors the still-image engine
        (``FACE_SWAP_KEEP_POD_RUNNING`` honoured).

        ``max_height`` defaults to ``VIDEO_SWAP_MAX_HEIGHT`` (else 1080): taller
        videos are downscaled in-graph to bound VRAM; raise it for a sharper
        background at the cost of more decode/encode time per frame.
        """
        if max_height is None:
            max_height = _envi("VIDEO_SWAP_MAX_HEIGHT", DEFAULT_MAX_HEIGHT)
        self._validate_image(source_image)
        self._validate_video(target_video)
        meta = self._probe_video(target_video)
        plan = plan_video_swap(
            meta,
            max_seconds=max_seconds,
            max_height=max_height,
            occlusion=_occlusion_enabled(),
        )
        self._fire(progress_cb, "planned", {
            "frames": plan.frame_count,
            "est_usd": plan.est_usd,
            "est_minutes": plan.est_minutes,
            "downscale_to": plan.downscale_to,
        })

        client = self._get_client()
        pod_id: str | None = None
        try:
            pod, pod_id, reused = await self._find_or_start_pod(client)
            self._fire(progress_cb, "pod_ready", {
                "pod_id": pod_id, "reused": reused,
            })
            pod_url = await client.get_pod_public_url(pod_id, port=8188)
            if not pod_url:
                raise FaceSwapError(
                    f"pod {pod_id} has no public URL on port 8188"
                )
            await self._ensure_comfyui_alive(pod_id, pod_url)
            reactor_class = await self._probe_reactor_class(pod_url)
            # Only probe for the occlusion node when the feature is enabled, so
            # the default path keeps its single /object_info round-trip.
            mask_helper_available = False
            if _occlusion_enabled():
                mask_helper_available = await self._probe_mask_helper_available(
                    pod_url
                )

            source_filename = await self._upload_image(pod_url, source_image)
            video_filename = await self._upload_image(pod_url, target_video)

            workflow = self._build_video_workflow(
                source_filename=source_filename,
                video_filename=video_filename,
                plan=plan,
                reactor_class=reactor_class,
                mask_helper_available=mask_helper_available,
            )
            prompt_id = await self._submit_prompt(pod_url, workflow)
            entry = await self._poll_until_done(pod_url, prompt_id)
            mp4_name = self._extract_mp4_filename(entry, prompt_id)
            out_path = await self._download_mp4(pod_url, mp4_name)
            self._fire(progress_cb, "done", {"output_path": out_path})
            return out_path
        finally:
            if pod_id is not None:
                keep = os.environ.get(_KEEP_POD_RUNNING_ENV, "").strip().lower()
                if keep in {"1", "true"}:
                    logger.info(
                        "VideoFaceSwapEngine: %s set; leaving pod %s running",
                        _KEEP_POD_RUNNING_ENV, pod_id,
                    )
                else:
                    try:
                        await client.stop_pod(pod_id)
                    except Exception as exc:  # noqa: BLE001 - cleanup
                        logger.warning(
                            "VideoFaceSwapEngine: stop_pod(%s) failed: %s",
                            pod_id, exc,
                        )
            await self._maybe_close()

    # ── internals ───────────────────────────────────────────────────────────

    @staticmethod
    def _validate_video(path: Path) -> None:
        p = Path(path)
        if not p.exists() or not p.is_file():
            raise ValueError(f"video not found: {p}")
        if p.suffix.lower() not in _VALID_VIDEO_SUFFIXES:
            raise ValueError(
                f"unsupported video extension {p.suffix!r}; "
                f"expected one of {sorted(_VALID_VIDEO_SUFFIXES)}"
            )

    def _probe_video(self, path: Path) -> VideoMeta:
        """Read fps/frame-count/dimensions via OpenCV (host-side, no pod)."""
        import cv2  # type: ignore[import-not-found]

        cap = cv2.VideoCapture(str(path))
        try:
            if not cap.isOpened():
                raise FaceSwapError(f"cv2 could not open video: {path}")
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        finally:
            cap.release()
        if fps <= 0 or frames <= 0:
            raise FaceSwapError(
                f"could not probe video (fps={fps}, frames={frames}): {path}"
            )
        return VideoMeta(fps=fps, frame_count=frames, width=width, height=height)

    async def _probe_mask_helper_available(self, pod_url: str) -> bool:
        """True if this ComfyUI registers the ReActorMaskHelper occlusion node.

        Best-effort: any probe failure (network error, non-200, bad JSON, or
        the class simply being absent) returns ``False`` so the engine falls
        back to the plain swap instead of submitting a graph that references a
        node this pod's ReActor pack doesn't ship.
        """
        http = self._get_http()
        try:
            r = await http.get(f"{pod_url}/object_info")
        except httpx.HTTPError as exc:
            logger.warning(
                "VideoFaceSwapEngine: /object_info probe failed (%s); "
                "occlusion mask disabled",
                exc,
            )
            return False
        if r.status_code != 200:
            logger.warning(
                "VideoFaceSwapEngine: /object_info HTTP %d; "
                "occlusion mask disabled",
                r.status_code,
            )
            return False
        try:
            info = r.json()
        except ValueError:
            return False
        return _MASK_HELPER_CLASS in info

    @staticmethod
    def _build_mask_helper_node() -> dict[str, Any]:
        """The ReActorMaskHelper node (id "5"), occlusion-corrected output.

        Inputs ``image`` (original frames) and ``swapped_image`` (the raw swap)
        are wired by the caller. The required params come from the node's live
        ``/object_info`` schema; the two model names and the SAM threshold are
        env-tunable. ``bbox_model_name`` MUST be a face-trained YOLO
        (``bbox/face_yolov8m.pt``) — a generic COCO ``yolov8m.pt`` detects
        "person", not the face region, and the mask would be wrong. The
        ``bbox/`` prefix is the Impact-Pack subfolder convention and is the
        exact value ComfyUI's dropdown expects; without it ``/prompt`` rejects
        the node with ``value_not_in_list`` (HTTP 400).
        """
        return {
            "inputs": {
                "image": ["1", 0],
                "swapped_image": ["3", 0],
                "bbox_model_name": _envs(
                    "VIDEO_SWAP_OCCLUSION_BBOX_MODEL", "bbox/face_yolov8m.pt"
                ),
                "bbox_threshold": _envf("VIDEO_SWAP_OCCLUSION_BBOX_THRESHOLD", 0.5),
                "bbox_dilation": 10,
                "bbox_crop_factor": 3.0,
                "bbox_drop_size": 10,
                "sam_model_name": _envs(
                    "VIDEO_SWAP_OCCLUSION_SAM_MODEL", "sam_vit_b_01ec64.pth"
                ),
                "sam_dilation": 0,
                "sam_threshold": _envf("VIDEO_SWAP_OCCLUSION_SAM_THRESHOLD", 0.93),
                "bbox_expansion": 0,
                "mask_hint_threshold": 0.7,
                "mask_hint_use_negative": "False",
                "morphology_operation": "dilate",
                "morphology_distance": 0,
                "blur_radius": 9,
                "sigma_factor": 1.0,
            },
            "class_type": "ReActorMaskHelper",
            "_meta": {"title": "ReActorMaskHelper (occlusion)"},
        }

    def _build_video_workflow(
        self,
        *,
        source_filename: str,
        video_filename: str,
        plan: VideoSwapPlan,
        reactor_class: str,
        mask_helper_available: bool = False,
    ) -> dict[str, Any]:
        try:
            with _VIDEO_WORKFLOW_FILE.open("r", encoding="utf-8") as fh:
                base = json.load(fh)
        except FileNotFoundError as exc:
            raise FaceSwapError(
                f"video workflow file missing: {_VIDEO_WORKFLOW_FILE}"
            ) from exc

        workflow = copy.deepcopy(base)
        workflow.pop("_comment", None)

        load_video = workflow.get("1")
        if not isinstance(load_video, dict) or "inputs" not in load_video:
            raise FaceSwapError("workflow node '1' (VHS_LoadVideo) malformed")
        load_video["inputs"]["video"] = video_filename
        load_video["inputs"]["frame_load_cap"] = plan.frame_load_cap
        if plan.downscale_to is not None:
            load_video["inputs"]["custom_width"] = plan.downscale_to[0]
            load_video["inputs"]["custom_height"] = plan.downscale_to[1]

        load_image = workflow.get("2")
        if not isinstance(load_image, dict) or "inputs" not in load_image:
            raise FaceSwapError("workflow node '2' (LoadImage) malformed")
        load_image["inputs"]["image"] = source_filename

        reactor = workflow.get("3")
        if not isinstance(reactor, dict) or "inputs" not in reactor:
            raise FaceSwapError("workflow node '3' (ReActor) malformed")
        reactor["class_type"] = reactor_class
        # Quality knob: lower GFPGAN restore blend to cut per-frame shimmer.
        # NB: codeformer_weight in the graph is inert while face_restore_model
        # is GFPGANv1.4.pth — it only applies to CodeFormer restore models.
        reactor["inputs"]["face_restore_visibility"] = _envf(
            "VIDEO_SWAP_FACE_RESTORE_VISIBILITY", DEFAULT_FACE_RESTORE_VISIBILITY
        )

        combine = workflow.get("4")
        if not isinstance(combine, dict) or "inputs" not in combine:
            raise FaceSwapError("workflow node '4' (VHS_VideoCombine) malformed")
        combine["inputs"]["frame_rate"] = int(round(plan.fps))

        # Occlusion: splice ReActorMaskHelper (node "5") between the swap (3)
        # and the combiner (4) so foreground objects in front of the face are
        # restored. Only when explicitly enabled AND the node exists on the pod;
        # otherwise the graph is byte-for-byte the original (safe default).
        if _occlusion_enabled() and mask_helper_available:
            workflow["5"] = self._build_mask_helper_node()
            combine["inputs"]["images"] = ["5", 0]

        return workflow

    @staticmethod
    def _extract_mp4_filename(entry: dict[str, Any], prompt_id: str) -> str:
        outputs = entry.get("outputs") or {}
        for node_outputs in outputs.values():
            if not isinstance(node_outputs, dict):
                continue
            for items in node_outputs.values():
                if not isinstance(items, list):
                    continue
                for item in items:
                    if isinstance(item, dict):
                        fn = item.get("filename")
                        if isinstance(fn, str) and fn.lower().endswith(".mp4"):
                            return fn
        raise FaceSwapError(
            f"no .mp4 in /history outputs for prompt {prompt_id}"
        )

    async def _download_mp4(self, pod_url: str, filename: str) -> Path:
        http = self._get_http()
        self._output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        target = self._output_dir / f"video_swap_{ts}.mp4"
        r = await http.get(
            f"{pod_url}/view",
            params={"filename": filename, "type": "output"},
        )
        if r.status_code >= 400:
            raise FaceSwapError(f"/view HTTP {r.status_code}: {r.text}")
        target.write_bytes(r.content)
        size = target.stat().st_size
        if size < _MIN_VIDEO_OUTPUT_BYTES:
            raise FaceSwapError(
                f"downloaded mp4 too small ({size} bytes) at {target}"
            )
        return target
