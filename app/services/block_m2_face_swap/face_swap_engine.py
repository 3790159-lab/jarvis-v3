# -*- coding: utf-8 -*-
"""Block M.2.5 face-swap engine — ReActor on RunPod ComfyUI.

Reuses :class:`RunpodClient` for pod lifecycle and replicates the ComfyUI
HTTP plumbing inline (we deliberately avoid going through
:class:`RunpodComfyEngine` because its single-shot generate() stops the pod
in a ``finally`` block — bad for batch swaps where we want to keep one pod
alive across N submissions).

Two entry points:

- :meth:`FaceSwapEngine.swap` — single source → single target → single image.
  Opens a pod, runs one swap, stops the pod.
- :meth:`FaceSwapEngine.swap_batch` — opens a pod **once**, runs N swaps,
  stops the pod. Returns ``[Path | None]`` so partial failures are visible.

At workflow-build time the engine probes ComfyUI's ``/object_info`` for the
canonical ``ReActorFaceSwap`` node class; if missing, it falls back to
``ReActorFaceSwapOpt`` (a variant shipped by some forks).
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.services.block_m2_video.runpod.runpod_client import (
    PodInfo,
    RunpodApiError,
    RunpodClient,
    RunpodExecUnavailable,
    RunpodSupplyError,
)
from app.services.block_m2_video.runpod.runpod_config import (
    RunpodConfig,
    get_runpod_config,
)

logger = logging.getLogger(__name__)


_VALID_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_WORKFLOW_FILE = (
    Path(__file__).resolve().parent / "workflows" / "face_swap_only.json"
)
_POD_NAME_PREFIX = "jarvis-m2-"  # share pods with Phase B (same ComfyUI image)
_POD_READY_TIMEOUT_SEC = 600
_DEFAULT_HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=5.0)
_UPLOAD_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=120.0, pool=5.0)
_POLL_INTERVAL_SEC = 3.0  # swap is ~10-30s, poll faster than video engine
_POLL_TIMEOUT_SEC = 300  # 5 min per swap is plenty
_MIN_OUTPUT_BYTES = 5 * 1024  # smallest plausible jpg
_COMFYUI_STARTUP_TIMEOUT_SEC = 120
_COMFYUI_HEALTH_TIMEOUT = httpx.Timeout(5.0)
_COMFYUI_HEALTH_POLL_INTERVAL_SEC = 5.0

_PRIMARY_REACTOR_CLASS = "ReActorFaceSwap"
_FALLBACK_REACTOR_CLASS = "ReActorFaceSwapOpt"


class FaceSwapError(RuntimeError):
    """Raised when a face-swap submission fails fatally."""


class FaceSwapEngine:
    """ReActor-based face-swap engine on RunPod ComfyUI."""

    engine_name = "runpod_reactor"
    model_name = "reactor-inswapper-128"

    def __init__(
        self,
        config: RunpodConfig | None = None,
        client: RunpodClient | None = None,
        *,
        output_dir: Path | None = None,
        http_client: httpx.AsyncClient | None = None,
        poll_interval_sec: float = _POLL_INTERVAL_SEC,
        poll_timeout_sec: int = _POLL_TIMEOUT_SEC,
        pod_ready_timeout_sec: int = _POD_READY_TIMEOUT_SEC,
    ) -> None:
        self._config = config
        self._client = client
        self._owns_client = client is None
        self._output_dir = output_dir or (
            Path(r"C:\jarvis\data\block_m2_face_swap\outputs")
        )
        self._http_client = http_client
        self._owns_http = http_client is None
        self._poll_interval_sec = poll_interval_sec
        self._poll_timeout_sec = poll_timeout_sec
        self._pod_ready_timeout_sec = pod_ready_timeout_sec

    # ── public API ──────────────────────────────────────────────────────────

    async def swap(self, source_image: Path, target_image: Path) -> Path:
        """One-shot swap: opens pod, runs swap, stops pod. Returns image path."""
        results = await self.swap_batch(source_image, [target_image])
        result = results[0]
        if result is None:
            raise FaceSwapError(
                f"swap failed for {target_image.name} (see logs)"
            )
        return result

    async def swap_batch(
        self,
        source_image: Path,
        target_images: list[Path],
        *,
        progress_cb=None,
        cancel_check=None,
    ) -> list[Path | None]:
        """Run N swaps on a single pod session.

        Args:
            source_image: Source face (uploaded once, reused for all targets).
            target_images: N target photos to swap into.
            progress_cb: Optional ``(stage, payload)`` callback. Stages fired:
                ``"pod_ready"`` (payload: ``pod_id``, ``reused``),
                ``"swap_started"`` (payload: ``index``, ``filename``),
                ``"swap_done"`` (payload: ``index``, ``output_path``),
                ``"swap_failed"`` (payload: ``index``, ``error``).
            cancel_check: Optional zero-arg callable returning ``True`` to
                abort the batch between submissions. The pod is still stopped
                in the ``finally`` block.

        Returns:
            List the same length as ``target_images``; entry is the saved
            image path on success, ``None`` on per-photo failure.
        """
        if not target_images:
            return []
        self._validate_image(source_image)
        for t in target_images:
            self._validate_image(t)

        client = self._get_client()
        pod_id: str | None = None
        pod: PodInfo | None = None
        results: list[Path | None] = [None] * len(target_images)

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

            await self._ensure_comfyui_alive(client, pod_id, pod_url)
            reactor_class = await self._probe_reactor_class(pod_url)

            source_filename = await self._upload_image(pod_url, source_image)

            for idx, target in enumerate(target_images):
                if cancel_check is not None:
                    try:
                        if cancel_check():
                            logger.info(
                                "FaceSwapEngine: cancel requested at idx=%d",
                                idx,
                            )
                            break
                    except Exception:  # noqa: BLE001
                        logger.exception("cancel_check raised; ignoring")

                self._fire(progress_cb, "swap_started", {
                    "index": idx, "filename": target.name,
                })
                try:
                    out_path = await self._swap_one(
                        pod_url,
                        source_filename=source_filename,
                        target_image=target,
                        reactor_class=reactor_class,
                        index=idx,
                    )
                    results[idx] = out_path
                    self._fire(progress_cb, "swap_done", {
                        "index": idx, "output_path": out_path,
                    })
                except Exception as exc:  # noqa: BLE001 - per-photo fault isolation
                    logger.exception(
                        "FaceSwapEngine: swap idx=%d failed", idx
                    )
                    self._fire(progress_cb, "swap_failed", {
                        "index": idx, "error": str(exc),
                    })

            return results
        finally:
            if pod_id is not None:
                try:
                    await client.stop_pod(pod_id)
                    logger.info(
                        "FaceSwapEngine: stopped pod %s after batch", pod_id
                    )
                except Exception as exc:  # noqa: BLE001 - cleanup
                    logger.warning(
                        "FaceSwapEngine: stop_pod(%s) failed: %s", pod_id, exc
                    )
            await self._maybe_close()

    # ── internals ───────────────────────────────────────────────────────────

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
    def _fire(cb, stage: str, payload: dict) -> None:
        if cb is None:
            return
        try:
            cb(stage, payload)
        except Exception:
            logger.exception("progress_cb stage=%s raised; ignoring", stage)

    @staticmethod
    def _validate_image(path: Path) -> None:
        p = Path(path)
        if not p.exists() or not p.is_file():
            raise ValueError(f"image not found: {p}")
        if p.suffix.lower() not in _VALID_IMAGE_SUFFIXES:
            raise ValueError(
                f"unsupported image extension {p.suffix!r}; "
                f"expected one of {sorted(_VALID_IMAGE_SUFFIXES)}"
            )

    async def _find_or_start_pod(
        self, client: RunpodClient
    ) -> tuple[PodInfo, str, bool]:
        try:
            pods = await client.list_pods()
        except RunpodApiError as exc:
            raise FaceSwapError(f"list_pods failed: {exc}") from exc

        candidates = [
            p for p in pods if (p.name or "").startswith(_POD_NAME_PREFIX)
        ]
        candidates.sort(
            key=lambda p: (p.last_status_change or "", p.id or ""),
            reverse=True,
        )

        for pod in candidates:
            if (pod.desired_status or "").upper() == "RUNNING":
                logger.info("FaceSwapEngine: reusing pod %s", pod.id)
                return pod, pod.id, True

        for pod in candidates:
            status = (pod.desired_status or "").upper()
            if status not in {"STOPPED", "EXITED"}:
                continue
            logger.info("FaceSwapEngine: resuming pod %s (%s)", pod.id, status)
            try:
                resumed = await client.resume_pod(pod.id)
                ready = await client.wait_for_ready(
                    resumed.id, timeout_sec=self._pod_ready_timeout_sec
                )
                return ready, ready.id, False
            except RunpodApiError as exc:
                logger.warning(
                    "FaceSwapEngine: resume(%s) failed: %s; trying next",
                    pod.id, exc,
                )
                continue

        new_name = f"{_POD_NAME_PREFIX}swap_{int(time.time())}"
        logger.info("FaceSwapEngine: spawning fresh pod %s", new_name)
        try:
            pod = await client.start_pod(name=new_name)
        except RunpodSupplyError as exc:
            raise FaceSwapError(
                f"no GPU supply for pod {new_name}: {exc}"
            ) from exc
        except RunpodApiError as exc:
            raise FaceSwapError(f"start_pod failed: {exc}") from exc

        try:
            ready = await client.wait_for_ready(
                pod.id, timeout_sec=self._pod_ready_timeout_sec
            )
        except RunpodApiError as exc:
            raise FaceSwapError(
                f"pod {pod.id} did not reach RUNNING: {exc}"
            ) from exc
        return ready, ready.id, False

    async def _ensure_comfyui_alive(
        self, client: RunpodClient, pod_id: str, pod_url: str
    ) -> None:
        if await self._comfyui_alive(pod_url):
            return
        logger.info(
            "FaceSwapEngine: ComfyUI not responding on %s; attempting auto-start",
            pod_url,
        )
        try:
            await client.execute_command(
                pod_id,
                "cd /workspace/ComfyUI && nohup python3 main.py "
                "--listen 0.0.0.0 --port 8188 > /tmp/comfyui.log 2>&1 &",
            )
        except RunpodExecUnavailable:
            logger.warning(
                "FaceSwapEngine: podExec unavailable; polling and hoping"
            )
        except RunpodApiError as exc:
            raise FaceSwapError(
                f"failed to start ComfyUI on pod {pod_id}: {exc}"
            ) from exc

        deadline = time.monotonic() + _COMFYUI_STARTUP_TIMEOUT_SEC
        while time.monotonic() < deadline:
            if await self._comfyui_alive(pod_url):
                return
            await asyncio.sleep(_COMFYUI_HEALTH_POLL_INTERVAL_SEC)
        raise FaceSwapError(f"ComfyUI failed to start on pod {pod_id}")

    async def _comfyui_alive(self, pod_url: str) -> bool:
        http = self._get_http()
        try:
            r = await http.get(
                f"{pod_url}/system_stats", timeout=_COMFYUI_HEALTH_TIMEOUT
            )
        except httpx.HTTPError:
            return False
        if r.status_code != 200:
            return False
        try:
            r.json()
        except ValueError:
            return False
        return True

    async def _probe_reactor_class(self, pod_url: str) -> str:
        """Return whichever ReActor node class is registered on this ComfyUI.

        On any probe failure we return the primary class name; if the workflow
        submit then fails with ``node_errors``, the caller surfaces a clear
        error message including the class name we tried.
        """
        http = self._get_http()
        try:
            r = await http.get(f"{pod_url}/object_info")
        except httpx.HTTPError as exc:
            logger.warning(
                "FaceSwapEngine: /object_info probe failed (%s); "
                "assuming %s",
                exc,
                _PRIMARY_REACTOR_CLASS,
            )
            return _PRIMARY_REACTOR_CLASS
        if r.status_code != 200:
            logger.warning(
                "FaceSwapEngine: /object_info HTTP %d; assuming %s",
                r.status_code,
                _PRIMARY_REACTOR_CLASS,
            )
            return _PRIMARY_REACTOR_CLASS
        try:
            info = r.json()
        except ValueError:
            return _PRIMARY_REACTOR_CLASS

        if _PRIMARY_REACTOR_CLASS in info:
            return _PRIMARY_REACTOR_CLASS
        if _FALLBACK_REACTOR_CLASS in info:
            logger.info(
                "FaceSwapEngine: %s not present; using fallback %s",
                _PRIMARY_REACTOR_CLASS,
                _FALLBACK_REACTOR_CLASS,
            )
            return _FALLBACK_REACTOR_CLASS

        logger.warning(
            "FaceSwapEngine: neither %s nor %s found in /object_info; "
            "submitting with %s and letting ComfyUI complain",
            _PRIMARY_REACTOR_CLASS,
            _FALLBACK_REACTOR_CLASS,
            _PRIMARY_REACTOR_CLASS,
        )
        return _PRIMARY_REACTOR_CLASS

    async def _upload_image(self, pod_url: str, image_path: Path) -> str:
        http = self._get_http()
        with image_path.open("rb") as fh:
            files = {"image": (image_path.name, fh, "application/octet-stream")}
            data = {"type": "input"}
            r = await http.post(
                f"{pod_url}/upload/image",
                files=files,
                data=data,
                timeout=_UPLOAD_TIMEOUT,
            )
        if r.status_code >= 400:
            raise FaceSwapError(
                f"/upload/image HTTP {r.status_code}: {r.text}"
            )
        try:
            payload = r.json()
        except ValueError as exc:
            raise FaceSwapError(f"/upload/image non-JSON: {exc}") from exc
        uploaded = payload.get("name") or image_path.name
        logger.info("FaceSwapEngine: uploaded %s -> %s", image_path.name, uploaded)
        return str(uploaded)

    def _build_workflow(
        self,
        source_filename: str,
        target_filename: str,
        reactor_class: str,
    ) -> dict[str, Any]:
        try:
            with _WORKFLOW_FILE.open("r", encoding="utf-8") as fh:
                base = json.load(fh)
        except FileNotFoundError as exc:
            raise FaceSwapError(
                f"workflow file missing: {_WORKFLOW_FILE}"
            ) from exc

        workflow = copy.deepcopy(base)
        workflow.pop("_comment", None)

        for node_id, image_name in (("1", source_filename), ("2", target_filename)):
            node = workflow.get(node_id)
            if not isinstance(node, dict) or "inputs" not in node:
                raise FaceSwapError(
                    f"workflow node {node_id!r} (LoadImage) missing/malformed"
                )
            node["inputs"]["image"] = image_name

        reactor_node = workflow.get("3")
        if not isinstance(reactor_node, dict):
            raise FaceSwapError("workflow node '3' (ReActor) missing")
        reactor_node["class_type"] = reactor_class

        return workflow

    async def _submit_prompt(
        self, pod_url: str, workflow: dict[str, Any]
    ) -> str:
        http = self._get_http()
        r = await http.post(f"{pod_url}/prompt", json={"prompt": workflow})
        if r.status_code >= 400:
            raise FaceSwapError(f"/prompt HTTP {r.status_code}: {r.text}")
        try:
            payload = r.json()
        except ValueError as exc:
            raise FaceSwapError(f"/prompt non-JSON: {exc}") from exc
        node_errors = payload.get("node_errors") or {}
        if node_errors:
            raise FaceSwapError(f"/prompt node_errors: {node_errors}")
        prompt_id = payload.get("prompt_id")
        if not prompt_id:
            raise FaceSwapError(f"/prompt returned no prompt_id: {payload}")
        return str(prompt_id)

    async def _poll_until_done(
        self, pod_url: str, prompt_id: str
    ) -> dict[str, Any]:
        http = self._get_http()
        deadline = time.monotonic() + self._poll_timeout_sec
        while True:
            r = await http.get(f"{pod_url}/history/{prompt_id}")
            if r.status_code >= 400:
                raise FaceSwapError(
                    f"/history HTTP {r.status_code}: {r.text}"
                )
            try:
                payload = r.json()
            except ValueError as exc:
                raise FaceSwapError(f"/history non-JSON: {exc}") from exc

            entry = payload.get(prompt_id) if isinstance(payload, dict) else None
            if isinstance(entry, dict):
                status = entry.get("status") or {}
                if status.get("completed"):
                    status_str = str(status.get("status_str", "")).lower()
                    if status_str != "success":
                        raise FaceSwapError(
                            f"prompt {prompt_id} finished status={status_str!r} "
                            f"messages={status.get('messages')}"
                        )
                    return entry
            if time.monotonic() >= deadline:
                raise FaceSwapError(
                    f"prompt {prompt_id} did not finish within "
                    f"{self._poll_timeout_sec}s"
                )
            await asyncio.sleep(self._poll_interval_sec)

    @staticmethod
    def _extract_image_filename(entry: dict[str, Any], prompt_id: str) -> str:
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
                    if not isinstance(fn, str):
                        continue
                    lo = fn.lower()
                    if (
                        lo.endswith(".png")
                        or lo.endswith(".jpg")
                        or lo.endswith(".jpeg")
                        or lo.endswith(".webp")
                    ):
                        return fn
        raise FaceSwapError(
            f"no image in /history outputs for prompt {prompt_id}"
        )

    async def _download_output(
        self, pod_url: str, filename: str, index: int
    ) -> Path:
        http = self._get_http()
        self._output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        suffix = Path(filename).suffix or ".png"
        target = self._output_dir / f"swap_{ts}_{index:02d}{suffix}"
        params = {"filename": filename, "type": "output"}
        r = await http.get(f"{pod_url}/view", params=params)
        if r.status_code >= 400:
            raise FaceSwapError(f"/view HTTP {r.status_code}: {r.text}")
        target.write_bytes(r.content)
        size = target.stat().st_size
        if size < _MIN_OUTPUT_BYTES:
            raise FaceSwapError(
                f"downloaded image too small ({size} bytes) at {target}"
            )
        return target

    async def _swap_one(
        self,
        pod_url: str,
        *,
        source_filename: str,
        target_image: Path,
        reactor_class: str,
        index: int,
    ) -> Path:
        target_filename = await self._upload_image(pod_url, target_image)
        workflow = self._build_workflow(
            source_filename, target_filename, reactor_class
        )
        prompt_id = await self._submit_prompt(pod_url, workflow)
        entry = await self._poll_until_done(pod_url, prompt_id)
        image_name = self._extract_image_filename(entry, prompt_id)
        return await self._download_output(pod_url, image_name, index)
