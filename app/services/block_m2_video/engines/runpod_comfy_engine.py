# -*- coding: utf-8 -*-
"""RunPod ComfyUI engine — Wan 2.2 i2v on a managed RunPod pod.

Phase B implementation. The pipeline is:

1. Validate the request and resolve a generation_id.
2. Find or spawn a ``jarvis-m2-*`` pod, wait for it to reach RUNNING.
3. Upload the input image to ComfyUI via ``/upload/image``.
4. Inject the uploaded filename, prompt, and seed into the
   ``wan22_i2v_v20`` workflow JSON, then submit via ``/prompt``.
5. Poll ``/history/{prompt_id}`` until completion.
6. Download the resulting MP4 via ``/view`` and return a
   :class:`VideoResult`.
7. Stop the pod in a ``finally`` block.

All RunPod-side errors are wrapped in :class:`RunpodComfyError` so the
router can fall back to Replicate without inspecting RunPod internals.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from ..litterbox_uploader import LitterboxError, upload_to_litterbox
from ..runpod.runpod_client import (
    PodInfo,
    RunpodApiError,
    RunpodClient,
    RunpodSupplyError,
)
from ..runpod.runpod_config import RunpodConfig, get_runpod_config
from .engine_protocol import VideoRequest, VideoResult, new_generation_id

logger = logging.getLogger(__name__)


_VALID_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_WORKFLOW_FILE = (
    Path(__file__).resolve().parent / "workflows" / "wan22_i2v_v20.json"
)
_DEFAULT_OUTPUT_DIR = Path(r"C:\jarvis\data\block_m2_video\outputs")
_DEFAULT_HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=5.0)
_UPLOAD_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=120.0, pool=5.0)
_POD_NAME_PREFIX = "jarvis-m2-"
_POD_READY_TIMEOUT_SEC = 600
_GENERATE_POLL_INTERVAL_SEC = 15.0
_GENERATE_POLL_TIMEOUT_SEC = 1800
_MIN_OUTPUT_BYTES = 100 * 1024
_COMFYUI_STARTUP_TIMEOUT_SEC = 120
_COMFYUI_HEALTH_TIMEOUT = httpx.Timeout(5.0)
_COMFYUI_HEALTH_POLL_INTERVAL_SEC = 5.0
# Fresh-spawn supply retry (Day-4-task-3). Sniper-style fixed interval —
# 30 attempts * 20s ≈ a 10-minute budget waiting for GPU availability.
# Modeled on scripts/runpod_gpu_sniper.py (which polls start_pod on a fixed
# interval). No jitter here: a single in-engine retry has no concurrent
# pollers to desync, and a fixed interval keeps the budget arithmetic exact.
_SUPPLY_RETRY_INTERVAL_SEC = 20.0
_SUPPLY_MAX_ATTEMPTS = 30
# Cold-start retry: a pod can spawn cleanly yet ComfyUI never come up (bad
# image cache, container init race, transient network). Terminate the bad
# pod and spawn a fresh one rather than failing the whole animation. Three
# attempts caps the worst-case at ~3 * (spawn + 120s startup) ≈ 9 minutes.
_COLD_START_MAX_ATTEMPTS = 3

# -- workflow contract (wan22_i2v_v20) ---------------------------------------
# VHS_VideoCombine (node 21) emits at FPS; PainterI2VAdvanced (node 15)
# generates `length` latent frames. Default 121 frames = 5.76 s at 21 fps.
FPS = 21
DEFAULT_FRAMES = 121
MIN_SECONDS = 1.0   # 21 frames floor
MAX_SECONDS = 15.0  # 315 frames ceiling — conservative for A100-80GB VRAM

# -- fps interpolation (Task C) ----------------------------------------------
# Native generation is 21 fps; higher output fps is produced by inserting a
# RIFE frame-interpolation node between VAEDecode (node 19) and
# VHS_VideoCombine (node 21).
#
# Schema VERIFIED on a live pod (Day-7 smoke test, /object_info): the custom
# node is ``custom_nodes/ComfyUI-VFI`` exposing class ``RIFEInterpolation``
# (display "RIFE Frame Interpolation"), NOT Fannovel16's "RIFE VFI". It takes
# an exact source/target fps (FLOAT) rather than an integer multiplier, so any
# target fps is reachable, and the only available model is ``flownet.pkl``.
# Required inputs: images, source_fps, target_fps, scale; model_name is
# optional (default flownet.pkl). fps > 21 stays feature-gated off (see
# quality_settings.fps_interpolation_enabled) until the flag is flipped.
_RIFE_NODE_ID = "22"
_RIFE_MODEL = "flownet.pkl"
_RIFE_CLASS = "RIFEInterpolation"
_VAEDECODE_NODE_ID = "19"
_VIDEOCOMBINE_NODE_ID = "21"


class RunpodComfyError(RuntimeError):
    """Raised when the RunPod / ComfyUI generation pipeline fails."""


class RunpodComfyEngine:
    """HQ video engine — Wan 2.2 i2v on RunPod ComfyUI."""

    engine_name = "runpod_comfy"
    model_name = "wan2.2-remix-i2v-14b"
    workflow_version = "v20"

    def __init__(
        self,
        config: RunpodConfig | None = None,
        client: RunpodClient | None = None,
        *,
        output_dir: Path | None = None,
        http_client: httpx.AsyncClient | None = None,
        poll_interval_sec: float = _GENERATE_POLL_INTERVAL_SEC,
        poll_timeout_sec: int = _GENERATE_POLL_TIMEOUT_SEC,
        pod_ready_timeout_sec: int = _POD_READY_TIMEOUT_SEC,
        supply_retry_interval_sec: float = _SUPPLY_RETRY_INTERVAL_SEC,
        supply_max_attempts: int = _SUPPLY_MAX_ATTEMPTS,
    ) -> None:
        self._config = config
        self._client = client
        self._owns_client = client is None
        self._output_dir = output_dir or _resolve_output_dir()
        self._http_client = http_client
        self._owns_http = http_client is None
        self._poll_interval_sec = poll_interval_sec
        self._poll_timeout_sec = poll_timeout_sec
        self._pod_ready_timeout_sec = pod_ready_timeout_sec
        self._supply_retry_interval_sec = supply_retry_interval_sec
        self._supply_max_attempts = supply_max_attempts

    # -- public surface -------------------------------------------------------

    async def is_available(self) -> bool:
        cfg = self._resolve_config()
        if not cfg.network_volume_id:
            logger.warning(
                "RunpodComfyEngine.is_available: network_volume_id not set"
            )
            return False
        client = self._get_client()
        try:
            gpus = await client.list_gpu_types()
        except RunpodApiError as exc:
            logger.warning("RunpodComfyEngine.is_available: %s", exc)
            return False
        wanted = {cfg.gpu_type_id}
        if cfg.gpu_fallback_id:
            wanted.add(cfg.gpu_fallback_id)
        ids = {g.id for g in gpus}
        ok = bool(wanted & ids)
        if not ok:
            logger.info(
                "RunpodComfyEngine.is_available: none of %s present in %d gpu types",
                wanted,
                len(ids),
            )
        return ok

    async def generate(self, request: VideoRequest) -> VideoResult:
        start_time = time.monotonic()
        self._validate_request(request)
        generation_id = request.generation_id or new_generation_id()
        logger.info(
            "RunpodComfyEngine.generate: starting %s persona=%s seconds=%d",
            generation_id,
            request.persona_id,
            request.seconds,
        )

        client = self._get_client()
        pod_id: str | None = None
        pod: PodInfo | None = None

        try:
            # Cold-start retry: if ComfyUI never comes up on a spawned pod,
            # terminate it and try again on a fresh pod. Workflow errors
            # downstream (upload/submit/poll) are NOT retried — they bubble
            # up unchanged.
            pod_url: str | None = None
            for cold_attempt in range(1, _COLD_START_MAX_ATTEMPTS + 1):
                pod, pod_id, reused = await self._find_or_start_pod(
                    client, generation_id
                )
                if not reused:
                    # TODO: Phase B.2 — actively run bootstrap.sh via execute_command
                    # and verify before proceeding (currently we trust the pod
                    # template's startup CMD to have run it).
                    pass

                pod_url = await client.get_pod_public_url(pod_id, port=8188)
                if not pod_url:
                    raise RunpodComfyError(
                        f"pod {pod_id} has no public URL on port 8188"
                    )
                logger.info("RunpodComfyEngine: pod URL resolved -> %s", pod_url)

                try:
                    await self._ensure_comfyui_alive(pod_id, pod_url)
                    break  # cold-start succeeded
                except RunpodComfyError as exc:
                    logger.warning(
                        "RunpodComfyEngine: ComfyUI cold-start failed on pod %s "
                        "(attempt %d/%d): %s",
                        pod_id,
                        cold_attempt,
                        _COLD_START_MAX_ATTEMPTS,
                        exc,
                    )
                    # Bad pod — terminate it (P22: terminate, not stop; a stopped
                    # pod is RETAINED and keeps billing container-disk storage).
                    try:
                        await client.terminate_pod(pod_id)
                        logger.info(
                            "RunpodComfyEngine: terminated bad pod %s after "
                            "cold-start failure",
                            pod_id,
                        )
                    except Exception as stop_exc:  # noqa: BLE001 - cleanup
                        logger.warning(
                            "RunpodComfyEngine: terminate_pod(%s) failed: %s",
                            pod_id,
                            stop_exc,
                        )
                    # Prevent the outer finally from re-stopping the same id.
                    pod_id = None
                    pod = None
                    pod_url = None
                    if cold_attempt >= _COLD_START_MAX_ATTEMPTS:
                        raise
                    logger.info(
                        "RunpodComfyEngine: spawning fresh pod for cold-start "
                        "retry (attempt %d/%d)",
                        cold_attempt + 1,
                        _COLD_START_MAX_ATTEMPTS,
                    )

            uploaded = await self._upload_image(pod_url, request.input_image_path)
            workflow = self._build_workflow(uploaded, request)
            prompt_id = await self._submit_prompt(pod_url, workflow)
            history_entry = await self._poll_until_done(pod_url, prompt_id)
            mp4_filename = self._extract_mp4_filename(history_entry, prompt_id)
            output_path = await self._download_output(
                pod_url, mp4_filename, generation_id
            )

            duration = time.monotonic() - start_time
            cost = (
                (duration / 3600.0) * pod.cost_per_hr
                if pod and pod.cost_per_hr
                else 0.0
            )
            logger.info(
                "RunpodComfyEngine.generate: %s done in %.1fs cost=$%.4f -> %s",
                generation_id,
                duration,
                cost,
                output_path,
            )
            result = VideoResult(
                generation_id=generation_id,
                persona_id=request.persona_id,
                output_path=output_path,
                engine=self.engine_name,
                model=self.model_name,
                seed=request.seed if request.seed is not None else 0,
                cost_usd=cost,
                duration_sec=duration,
                timestamp=datetime.now(timezone.utc),
                prompt=request.prompt,
                seconds=request.seconds,
                extra={
                    "prompt_id": prompt_id,
                    "pod_id": pod_id,
                    "workflow_version": self.workflow_version,
                    "fps": getattr(request, "fps", FPS) or FPS,
                },
            )
            try:
                result.public_url = await upload_to_litterbox(
                    output_path, retention="24h"
                )
            except LitterboxError as exc:
                logger.warning(
                    "Litterbox upload failed; returning local-only result: %s",
                    exc,
                )
            return result
        except RunpodComfyError:
            raise
        except RunpodApiError as exc:
            logger.error("RunpodComfyEngine: RunPod API error: %s", exc)
            raise RunpodComfyError(f"RunPod API error: {exc}") from exc
        except httpx.HTTPError as exc:
            logger.error("RunpodComfyEngine: HTTP error: %s", exc)
            raise RunpodComfyError(f"HTTP error: {exc}") from exc
        finally:
            if pod_id is not None:
                try:
                    # P22: terminate (not stop) — a stopped pod is retained and
                    # keeps billing container-disk storage (~$0.10-0.20/GB/mo).
                    await client.terminate_pod(pod_id)
                    logger.info(
                        "RunpodComfyEngine: terminated pod %s after generation",
                        pod_id,
                    )
                except Exception as exc:  # noqa: BLE001 - cleanup must not raise
                    logger.warning(
                        "RunpodComfyEngine: terminate_pod(%s) failed: %s",
                        pod_id,
                        exc,
                    )
            await self._maybe_close()

    # -- internals ------------------------------------------------------------

    def _resolve_config(self) -> RunpodConfig:
        if self._config is None:
            self._config = get_runpod_config()
        return self._config

    def _get_client(self) -> RunpodClient:
        if self._client is None:
            self._client = RunpodClient(config=self._resolve_config())
        return self._client

    def _get_http(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=_DEFAULT_HTTP_TIMEOUT)
        return self._http_client

    async def _maybe_close(self) -> None:
        if self._owns_http and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _validate_request(request: VideoRequest) -> None:
        path = request.input_image_path
        if not isinstance(path, Path):
            path = Path(path)
        if not path.exists():
            raise ValueError(f"input_image_path does not exist: {path}")
        if not path.is_file():
            raise ValueError(f"input_image_path is not a file: {path}")
        suffix = path.suffix.lower()
        if suffix not in _VALID_IMAGE_SUFFIXES:
            raise ValueError(
                f"input_image_path has unsupported extension {suffix!r}; "
                f"expected one of {sorted(_VALID_IMAGE_SUFFIXES)}"
            )

    async def _find_or_start_pod(
        self, client: RunpodClient, generation_id: str
    ) -> tuple[PodInfo, str, bool]:
        try:
            pods = await client.list_pods()
        except RunpodApiError as exc:
            raise RunpodComfyError(f"list_pods failed: {exc}") from exc

        candidates = [
            p for p in pods if (p.name or "").startswith(_POD_NAME_PREFIX)
        ]
        # Newest first: prefer last_status_change, fall back to id as a
        # stable tiebreaker. RunPod pod ids are roughly time-ordered.
        candidates.sort(
            key=lambda p: (p.last_status_change or "", p.id or ""),
            reverse=True,
        )

        # Only RUNNING pods are valid reuse candidates. We deliberately do
        # NOT resume STOPPED/EXITED pods: RunPod runs the video-gen template's
        # startCmd / bootstrap.sh (ffmpeg, opencv, sqlalchemy, ...) only on
        # initial creation, not on resume — so a resumed pod comes up without
        # ComfyUI ever launching and the engine polls until it times out
        # (the B-48 regression observed in the Day-5 E2E test, e.g. resumed
        # pod r07nqxdzqu5a48). A fresh spawn is the only path that guarantees
        # the bootstrap runs.
        for pod in candidates:
            if (pod.desired_status or "").upper() == "RUNNING":
                logger.info("Reusing pod %s", pod.id)
                return pod, pod.id, True

        # No RUNNING candidate → spawn a fresh pod (retrying on supply
        # constraint rather than failing immediately).
        new_name = f"{_POD_NAME_PREFIX}{generation_id}"
        logger.info("Spawning new pod %s", new_name)
        pod = await self._spawn_with_supply_retry(client, new_name)

        try:
            ready = await client.wait_for_ready(
                pod.id, timeout_sec=self._pod_ready_timeout_sec
            )
        except RunpodApiError as exc:
            raise RunpodComfyError(
                f"pod {pod.id} did not reach RUNNING: {exc}"
            ) from exc
        return ready, ready.id, False

    async def _spawn_with_supply_retry(
        self, client: RunpodClient, name: str
    ) -> PodInfo:
        """Spawn a fresh pod, retrying while RunPod reports no GPU supply.

        Sniper-style (see scripts/runpod_gpu_sniper.py): when ``start_pod``
        raises :class:`RunpodSupplyError` (SUPPLY_CONSTRAINT — no free GPUs),
        wait ``_supply_retry_interval_sec`` and try again, up to
        ``_supply_max_attempts`` (≈10 min budget at 20s). A non-supply
        :class:`RunpodApiError` is not transient, so it fails immediately.
        If the budget is exhausted, re-raise :class:`RunpodSupplyError` with
        the attempt count and elapsed time so the timeout is diagnosable.
        """
        start = time.monotonic()
        last_exc: RunpodSupplyError | None = None
        for attempt in range(1, self._supply_max_attempts + 1):
            try:
                return await client.start_pod(name=name)
            except RunpodSupplyError as exc:
                last_exc = exc
                elapsed = time.monotonic() - start
                if attempt >= self._supply_max_attempts:
                    break
                logger.info(
                    "start_pod %s hit SUPPLY_CONSTRAINT (attempt %d/%d, "
                    "%.0fs elapsed): %s | retrying in %.0fs",
                    name,
                    attempt,
                    self._supply_max_attempts,
                    elapsed,
                    exc,
                    self._supply_retry_interval_sec,
                )
                await asyncio.sleep(self._supply_retry_interval_sec)
            except RunpodApiError as exc:
                raise RunpodComfyError(f"start_pod failed: {exc}") from exc

        elapsed = time.monotonic() - start
        raise RunpodSupplyError(
            f"no GPU supply for pod {name} after "
            f"{self._supply_max_attempts} attempts ({elapsed:.0f}s elapsed): "
            f"{last_exc}"
        )

    async def _ensure_comfyui_alive(self, pod_id: str, pod_url: str) -> None:
        """Wait until ComfyUI answers on ``pod_url``, polling /system_stats.

        The pod template's startup CMD is responsible for launching ComfyUI;
        we do not shell into the pod to auto-start it (B-48 removed the
        podExec fallback, which was unavailable on this account and only
        delayed the same polling loop).
        """
        if await self._comfyui_alive(pod_url):
            logger.info("ComfyUI alive at %s", pod_url)
            return

        logger.info(
            "ComfyUI not yet responding on %s; polling until ready on pod %s "
            "(pod template startup CMD launches it)",
            pod_url,
            pod_id,
        )
        deadline = time.monotonic() + _COMFYUI_STARTUP_TIMEOUT_SEC
        while time.monotonic() < deadline:
            if await self._comfyui_alive(pod_url):
                logger.info("ComfyUI alive at %s", pod_url)
                return
            await asyncio.sleep(_COMFYUI_HEALTH_POLL_INTERVAL_SEC)
        raise RunpodComfyError(f"ComfyUI failed to start on pod {pod_id}")

    async def _comfyui_alive(self, pod_url: str) -> bool:
        http = self._get_http()
        try:
            response = await http.get(
                f"{pod_url}/system_stats", timeout=_COMFYUI_HEALTH_TIMEOUT
            )
        except httpx.HTTPError:
            return False
        if response.status_code != 200:
            return False
        try:
            response.json()
        except ValueError:
            return False
        return True

    async def _upload_image(self, pod_url: str, image_path: Path) -> str:
        http = self._get_http()
        with image_path.open("rb") as fh:
            files = {"image": (image_path.name, fh, "application/octet-stream")}
            data = {"type": "input"}
            response = await http.post(
                f"{pod_url}/upload/image",
                files=files,
                data=data,
                timeout=_UPLOAD_TIMEOUT,
            )
        if response.status_code >= 400:
            raise RunpodComfyError(
                f"/upload/image HTTP {response.status_code}: {response.text}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RunpodComfyError(
                f"/upload/image returned non-JSON: {exc}"
            ) from exc
        uploaded = payload.get("name") or image_path.name
        logger.info("RunpodComfyEngine: uploaded image -> %s", uploaded)
        return str(uploaded)

    def _build_workflow(
        self, uploaded_image: str, request: VideoRequest
    ) -> dict[str, Any]:
        try:
            with _WORKFLOW_FILE.open("r", encoding="utf-8") as fh:
                base = json.load(fh)
        except FileNotFoundError as exc:
            raise RunpodComfyError(
                f"workflow file missing: {_WORKFLOW_FILE}"
            ) from exc

        workflow = copy.deepcopy(base)
        workflow.pop("_comment", None)

        load_image = workflow.get("5")
        if not isinstance(load_image, dict) or "inputs" not in load_image:
            raise RunpodComfyError(
                "workflow node '5' (LoadImage) missing or malformed"
            )
        load_image["inputs"]["image"] = uploaded_image

        positive = workflow.get("7")
        if (
            isinstance(positive, dict)
            and isinstance(positive.get("inputs"), dict)
            and request.prompt
        ):
            positive["inputs"]["text"] = request.prompt

        if request.seed is not None:
            for node in workflow.values():
                if (
                    isinstance(node, dict)
                    and node.get("class_type") == "KSamplerAdvanced"
                    and isinstance(node.get("inputs"), dict)
                ):
                    node["inputs"]["noise_seed"] = int(request.seed)

        if request.seconds:
            # int(x + 0.5) is half-up rounding; Python's round() uses banker's
            # rounding which would give the wrong answer for x.5 boundaries.
            target = int(request.seconds * FPS + 0.5)
            frames = max(
                int(MIN_SECONDS * FPS), min(int(MAX_SECONDS * FPS), target)
            )
            workflow["15"]["inputs"]["length"] = frames

        self._apply_fps(workflow, getattr(request, "fps", FPS) or FPS)
        return workflow

    @staticmethod
    def _apply_fps(workflow: dict[str, Any], fps: int) -> None:
        """Set output fps on VHS_VideoCombine and, for fps > 21, splice a RIFE
        interpolation node between VAEDecode and VHS_VideoCombine.

        ``length`` (native diffusion frames) is unchanged by fps — interpolation
        multiplies the *decoded* frames, so the VRAM/time budget stays bounded.
        """
        combine = workflow.get(_VIDEOCOMBINE_NODE_ID)
        if not isinstance(combine, dict) or not isinstance(
            combine.get("inputs"), dict
        ):
            raise RunpodComfyError(
                f"workflow node {_VIDEOCOMBINE_NODE_ID!r} "
                "(VHS_VideoCombine) missing or malformed"
            )
        combine["inputs"]["frame_rate"] = fps

        if fps <= FPS:
            return  # native fps — no interpolation needed

        # Re-route: VAEDecode → RIFE → VHS_VideoCombine. RIFEInterpolation
        # (ComfyUI-VFI) interpolates from source_fps to target_fps exactly, so
        # any fps > 21 is reachable (not just integer multiples of 21).
        decoded_ref = combine["inputs"].get("images", [_VAEDECODE_NODE_ID, 0])
        workflow[_RIFE_NODE_ID] = {
            "inputs": {
                "images": decoded_ref,
                "source_fps": float(FPS),
                "target_fps": float(fps),
                "scale": 1.0,
                "model_name": _RIFE_MODEL,
            },
            "class_type": _RIFE_CLASS,
            "_meta": {"title": "RIFE Frame Interpolation (Jarvis fps boost)"},
        }
        combine["inputs"]["images"] = [_RIFE_NODE_ID, 0]

    async def _submit_prompt(
        self, pod_url: str, workflow: dict[str, Any]
    ) -> str:
        http = self._get_http()
        response = await http.post(
            f"{pod_url}/prompt", json={"prompt": workflow}
        )
        if response.status_code >= 400:
            raise RunpodComfyError(
                f"/prompt HTTP {response.status_code}: {response.text}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RunpodComfyError(f"/prompt returned non-JSON: {exc}") from exc
        node_errors = payload.get("node_errors") or {}
        if node_errors:
            raise RunpodComfyError(f"/prompt node_errors: {node_errors}")
        prompt_id = payload.get("prompt_id")
        if not prompt_id:
            raise RunpodComfyError(f"/prompt returned no prompt_id: {payload}")
        logger.info("RunpodComfyEngine: submitted prompt_id=%s", prompt_id)
        return str(prompt_id)

    async def _poll_until_done(
        self, pod_url: str, prompt_id: str
    ) -> dict[str, Any]:
        http = self._get_http()
        deadline = time.monotonic() + self._poll_timeout_sec
        while True:
            response = await http.get(f"{pod_url}/history/{prompt_id}")
            if response.status_code >= 400:
                raise RunpodComfyError(
                    f"/history HTTP {response.status_code}: {response.text}"
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise RunpodComfyError(
                    f"/history returned non-JSON: {exc}"
                ) from exc

            entry = payload.get(prompt_id) if isinstance(payload, dict) else None
            if isinstance(entry, dict):
                status = entry.get("status") or {}
                if status.get("completed"):
                    status_str = str(status.get("status_str", "")).lower()
                    if status_str != "success":
                        raise RunpodComfyError(
                            f"prompt {prompt_id} finished status={status_str!r} "
                            f"messages={status.get('messages')}"
                        )
                    return entry

            if time.monotonic() >= deadline:
                raise RunpodComfyError(
                    f"prompt {prompt_id} did not finish within "
                    f"{self._poll_timeout_sec}s"
                )
            await asyncio.sleep(self._poll_interval_sec)

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
                    if not isinstance(item, dict):
                        continue
                    fn = item.get("filename")
                    if isinstance(fn, str) and fn.lower().endswith(".mp4"):
                        return fn
        raise RunpodComfyError(
            f"no .mp4 in /history outputs for prompt {prompt_id}"
        )

    async def _download_output(
        self, pod_url: str, filename: str, generation_id: str
    ) -> Path:
        http = self._get_http()
        self._output_dir.mkdir(parents=True, exist_ok=True)
        target = self._output_dir / f"{generation_id}.mp4"
        params = {"filename": filename, "type": "output"}
        response = await http.get(f"{pod_url}/view", params=params)
        if response.status_code >= 400:
            raise RunpodComfyError(
                f"/view HTTP {response.status_code}: {response.text}"
            )
        target.write_bytes(response.content)
        size = target.stat().st_size
        if size < _MIN_OUTPUT_BYTES:
            raise RunpodComfyError(
                f"downloaded mp4 too small ({size} bytes) at {target}"
            )
        logger.info(
            "RunpodComfyEngine: downloaded %s (%d bytes) -> %s",
            filename,
            size,
            target,
        )
        return target


def _resolve_output_dir() -> Path:
    raw = os.environ.get("M2_OUTPUT_DIR")
    if raw:
        return Path(raw)
    return _DEFAULT_OUTPUT_DIR
