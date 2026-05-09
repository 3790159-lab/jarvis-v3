# -*- coding: utf-8 -*-
"""Async client for a remote ComfyUI instance.

Phase 2 covers the REST surface needed to submit a workflow, poll for
completion, fetch outputs, and cancel — no WebSocket. Polling
``/history/{prompt_id}`` is sufficient for our cadence.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


_DEFAULT_TIMEOUT = 30.0


class ComfyUIError(Exception):
    """Raised on transport, HTTP, or workflow failures from ComfyUI."""

    def __init__(
        self,
        message: str,
        prompt_id: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.prompt_id = prompt_id
        self.status_code = status_code

    def __str__(self) -> str:  # pragma: no cover - trivial
        parts = [self.message]
        if self.prompt_id:
            parts.append(f"prompt_id={self.prompt_id}")
        if self.status_code is not None:
            parts.append(f"status={self.status_code}")
        return " | ".join(parts)


class FileRef(BaseModel):
    filename: str
    subfolder: str = ""
    type: str = "output"

    model_config = {"extra": "ignore"}


class WorkflowResult(BaseModel):
    prompt_id: str
    status: Literal["completed", "failed", "cancelled"]
    outputs: dict[str, list[FileRef]] = Field(default_factory=dict)
    error: str | None = None
    raw: dict[str, Any] | None = None

    model_config = {"extra": "ignore"}


def _mask_token(token: str | None) -> str:
    if not token:
        return "***"
    if len(token) <= 8:
        return "***"
    return f"{token[:3]}...{token[-3:]}"


class ComfyUIClient:
    """Thin async wrapper around the ComfyUI REST API."""

    def __init__(
        self,
        base_url: str,
        auth_token: str | None = None,
        client_id: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth_token = auth_token
        self._client_id = client_id or uuid.uuid4().hex
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

    # -- lifecycle ------------------------------------------------------------

    async def __aenter__(self) -> "ComfyUIClient":
        self._ensure_http()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    def _ensure_http(self) -> httpx.AsyncClient:
        if self._http is None:
            headers: dict[str, str] = {}
            if self._auth_token:
                headers["Authorization"] = f"Bearer {self._auth_token}"
            self._http = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                headers=headers,
            )
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    @property
    def client_id(self) -> str:
        return self._client_id

    # -- low-level helpers ---------------------------------------------------

    async def _request(
        self, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        http = self._ensure_http()
        masked = _mask_token(self._auth_token)
        logger.debug(
            "ComfyUI %s %s%s (auth=%s)",
            method,
            self._base_url,
            path,
            masked,
        )
        try:
            return await http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            logger.error("ComfyUI transport error %s %s: %s", method, path, exc)
            raise ComfyUIError(f"transport error: {exc}") from exc

    # -- endpoints -----------------------------------------------------------

    async def health_check(self) -> bool:
        """Return True iff ``/system_stats`` returns a 2xx response."""
        try:
            response = await self._request("GET", "/system_stats")
        except ComfyUIError:
            return False
        return 200 <= response.status_code < 300

    async def get_object_info(self) -> dict[str, Any]:
        response = await self._request("GET", "/object_info")
        if response.status_code >= 400:
            raise ComfyUIError(
                f"object_info HTTP {response.status_code}",
                status_code=response.status_code,
            )
        return response.json()

    async def submit_workflow(
        self,
        workflow: dict[str, Any],
        *,
        client_id: str | None = None,
    ) -> str:
        body = {
            "prompt": workflow,
            "client_id": client_id or self._client_id,
        }
        response = await self._request("POST", "/prompt", json=body)
        if response.status_code >= 400:
            raise ComfyUIError(
                f"submit_workflow HTTP {response.status_code}: {response.text}",
                status_code=response.status_code,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise ComfyUIError(f"submit_workflow non-JSON: {exc}") from exc
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise ComfyUIError(
                f"submit_workflow returned no prompt_id: {data}"
            )
        logger.info("ComfyUI submitted workflow prompt_id=%s", prompt_id)
        return str(prompt_id)

    async def get_history(self, prompt_id: str) -> dict[str, Any] | None:
        response = await self._request("GET", f"/history/{prompt_id}")
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise ComfyUIError(
                f"get_history HTTP {response.status_code}: {response.text}",
                prompt_id=prompt_id,
                status_code=response.status_code,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise ComfyUIError(
                f"get_history non-JSON: {exc}", prompt_id=prompt_id
            ) from exc
        if not data:
            # ComfyUI returns `{}` while the prompt is still queued.
            return None
        entry = data.get(prompt_id)
        return entry if entry is not None else None

    async def get_queue(self) -> dict[str, Any]:
        response = await self._request("GET", "/queue")
        if response.status_code >= 400:
            raise ComfyUIError(
                f"get_queue HTTP {response.status_code}",
                status_code=response.status_code,
            )
        return response.json()

    async def wait_for_completion(
        self,
        prompt_id: str,
        *,
        timeout_sec: int = 1200,
        poll_interval: float = 2.0,
    ) -> WorkflowResult:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout_sec
        while True:
            entry = await self.get_history(prompt_id)
            if entry is not None:
                return _parse_history_entry(prompt_id, entry)
            if loop.time() >= deadline:
                raise ComfyUIError(
                    f"workflow {prompt_id} did not finish within {timeout_sec}s",
                    prompt_id=prompt_id,
                )
            await asyncio.sleep(poll_interval)

    async def download_output(
        self,
        filename: str,
        subfolder: str = "",
        type_: str = "output",
    ) -> bytes:
        params = {"filename": filename, "subfolder": subfolder, "type": type_}
        response = await self._request("GET", "/view", params=params)
        if response.status_code >= 400:
            raise ComfyUIError(
                f"download_output HTTP {response.status_code}",
                status_code=response.status_code,
            )
        return response.content

    async def cancel(self, prompt_id: str) -> bool:
        """Interrupt the current job and try to remove it from the queue.

        Returns True if at least one of the two underlying requests
        succeeded (interrupt or queue-delete).
        """
        interrupt = await self._request("POST", "/interrupt")
        delete = await self._request(
            "POST", "/queue", json={"delete": [prompt_id]}
        )
        ok = (200 <= interrupt.status_code < 300) or (
            200 <= delete.status_code < 300
        )
        if not ok:
            logger.warning(
                "ComfyUI cancel(%s): interrupt=%s, queue-delete=%s",
                prompt_id,
                interrupt.status_code,
                delete.status_code,
            )
        return ok


def _parse_history_entry(prompt_id: str, entry: dict[str, Any]) -> WorkflowResult:
    """Translate a `/history/{prompt_id}` payload into a WorkflowResult."""
    status_payload = entry.get("status") or {}
    status_str = str(status_payload.get("status_str", "")).lower()

    outputs_raw = entry.get("outputs") or {}
    outputs: dict[str, list[FileRef]] = {}
    for node_id, node_outputs in outputs_raw.items():
        files: list[FileRef] = []
        if isinstance(node_outputs, dict):
            for key in ("images", "gifs", "videos", "files"):
                items = node_outputs.get(key)
                if not isinstance(items, list):
                    continue
                for item in items:
                    if isinstance(item, dict) and item.get("filename"):
                        files.append(FileRef.model_validate(item))
        if files:
            outputs[str(node_id)] = files

    if status_str == "success" or (status_payload.get("completed") and outputs):
        return WorkflowResult(
            prompt_id=prompt_id, status="completed", outputs=outputs, raw=entry
        )

    error_msg = None
    messages = status_payload.get("messages") or []
    for msg in messages:
        if isinstance(msg, list) and len(msg) >= 2 and msg[0] == "execution_error":
            error_msg = str(msg[1])
            break
    if status_str == "error" or error_msg:
        return WorkflowResult(
            prompt_id=prompt_id,
            status="failed",
            outputs=outputs,
            error=error_msg or "execution error",
            raw=entry,
        )

    return WorkflowResult(
        prompt_id=prompt_id,
        status="cancelled" if status_str == "interrupted" else "failed",
        outputs=outputs,
        error=error_msg,
        raw=entry,
    )
