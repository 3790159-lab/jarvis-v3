# -*- coding: utf-8 -*-
"""Async client for the RunPod GraphQL API.

Phase 1 covers account info, GPU/Pod listing, and pod lifecycle (start /
stop / terminate / wait). All requests go through :meth:`RunpodClient._gql`
which centralises auth, timeouts, logging, and error handling.

Real RunPod calls are NOT exercised in the test suite — every test uses
:mod:`unittest.mock` to stub out the underlying ``httpx.AsyncClient``.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from pydantic import BaseModel, Field

from .runpod_config import RunpodConfig, get_runpod_config

logger = logging.getLogger(__name__)


_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0)


def _mask_key(key: str) -> str:
    """Return a redacted version of an API key safe for logs."""
    if not key or len(key) < 12:
        return "***"
    return f"{key[:6]}...{key[-4:]}"


class RunpodApiError(Exception):
    """Raised when the RunPod API returns an error or unexpected payload."""

    def __init__(
        self,
        message: str,
        query_name: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.query_name = query_name
        self.status_code = status_code

    def __str__(self) -> str:  # pragma: no cover - trivial
        parts = [self.message]
        if self.query_name:
            parts.append(f"query={self.query_name}")
        if self.status_code is not None:
            parts.append(f"status={self.status_code}")
        return " | ".join(parts)


class GpuType(BaseModel):
    """A GPU offering returned by ``gpuTypes`` in the RunPod API."""

    id: str
    display_name: str = Field(alias="displayName")
    memory_in_gb: int | None = Field(default=None, alias="memoryInGb")
    secure_price: float | None = Field(default=None, alias="securePrice")
    community_price: float | None = Field(default=None, alias="communityPrice")

    model_config = {"populate_by_name": True, "extra": "ignore"}


class PodInfo(BaseModel):
    """Subset of pod fields the rest of the codebase relies on."""

    id: str
    name: str | None = None
    desired_status: str | None = Field(default=None, alias="desiredStatus")
    cost_per_hr: float | None = Field(default=None, alias="costPerHr")
    image_name: str | None = Field(default=None, alias="imageName")
    machine_id: str | None = Field(default=None, alias="machineId")
    gpu_count: int | None = Field(default=None, alias="gpuCount")
    last_status_change: str | None = Field(default=None, alias="lastStatusChange")
    runtime: dict[str, Any] | None = None
    raw: dict[str, Any] | None = None

    model_config = {"populate_by_name": True, "extra": "ignore"}

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "PodInfo":
        """Build a :class:`PodInfo` from a raw GraphQL pod dict."""
        return cls(**payload, raw=payload)


# ---------------------------------------------------------------------------
# GraphQL fragments
# ---------------------------------------------------------------------------

_POD_FIELDS = """
id
name
desiredStatus
costPerHr
imageName
machineId
gpuCount
lastStatusChange
runtime {
  ports {
    ip
    isIpPublic
    privatePort
    publicPort
    type
  }
  uptimeInSeconds
}
"""


class RunpodClient:
    """Thin async wrapper around the RunPod GraphQL API."""

    def __init__(
        self,
        config: RunpodConfig | None = None,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config or get_runpod_config()
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)

    # -- async context manager ------------------------------------------------

    async def __aenter__(self) -> "RunpodClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    # -- low-level GraphQL ----------------------------------------------------

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._config.api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }

    async def _gql(
        self,
        query: str,
        *,
        query_name: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"query": query}
        if variables is not None:
            body["variables"] = variables

        masked = _mask_key(self._config.api_key.get_secret_value())
        logger.debug(
            "RunPod GraphQL %s -> %s (key=%s, vars=%s)",
            query_name,
            self._config.api_endpoint,
            masked,
            variables,
        )

        try:
            response = await self._http.post(
                self._config.api_endpoint,
                json=body,
                headers=self._headers,
            )
        except httpx.HTTPError as exc:
            logger.error("RunPod GraphQL transport error in %s: %s", query_name, exc)
            raise RunpodApiError(
                f"transport error: {exc}", query_name=query_name
            ) from exc

        if response.status_code >= 400:
            text = response.text
            logger.error(
                "RunPod GraphQL %s HTTP %s: %s",
                query_name,
                response.status_code,
                text,
            )
            raise RunpodApiError(
                f"HTTP {response.status_code}: {text}",
                query_name=query_name,
                status_code=response.status_code,
            )

        try:
            data = response.json()
        except ValueError as exc:
            logger.error("RunPod GraphQL %s returned non-JSON: %s", query_name, exc)
            raise RunpodApiError(
                f"non-JSON response: {exc}",
                query_name=query_name,
                status_code=response.status_code,
            ) from exc

        if isinstance(data, dict) and data.get("errors"):
            logger.error(
                "RunPod GraphQL %s returned errors: %s (full response: %s)",
                query_name,
                data["errors"],
                data,
            )
            raise RunpodApiError(
                f"GraphQL errors: {data['errors']}",
                query_name=query_name,
                status_code=response.status_code,
            )

        if not isinstance(data, dict) or "data" not in data:
            logger.error(
                "RunPod GraphQL %s returned malformed payload: %s", query_name, data
            )
            raise RunpodApiError(
                "malformed GraphQL payload (missing 'data')",
                query_name=query_name,
                status_code=response.status_code,
            )

        return data["data"]

    # -- account / metadata ---------------------------------------------------

    async def get_account_info(self) -> dict[str, Any]:
        query = "query AccountInfo { myself { id email } }"
        data = await self._gql(query, query_name="AccountInfo")
        myself = data.get("myself") or {}
        if not isinstance(myself, dict):
            raise RunpodApiError(
                "missing 'myself' in account info", query_name="AccountInfo"
            )
        return myself

    async def list_gpu_types(self) -> list[GpuType]:
        query = (
            "query GpuTypes { gpuTypes { id displayName memoryInGb "
            "securePrice communityPrice } }"
        )
        data = await self._gql(query, query_name="GpuTypes")
        items = data.get("gpuTypes") or []
        return [GpuType.model_validate(item) for item in items]

    # -- pods -----------------------------------------------------------------

    async def list_pods(self) -> list[PodInfo]:
        query = f"query MyPods {{ myself {{ pods {{ {_POD_FIELDS} }} }} }}"
        data = await self._gql(query, query_name="MyPods")
        myself = data.get("myself") or {}
        pods = myself.get("pods") or []
        return [PodInfo.from_api(p) for p in pods]

    async def get_pod(self, pod_id: str) -> PodInfo | None:
        query = (
            f"query GetPod($input: PodFilter!) {{ pod(input: $input) "
            f"{{ {_POD_FIELDS} }} }}"
        )
        try:
            data = await self._gql(
                query,
                query_name="GetPod",
                variables={"input": {"podId": pod_id}},
            )
        except RunpodApiError as exc:
            if exc.status_code == 404:
                return None
            raise
        pod = data.get("pod")
        if not pod:
            return None
        return PodInfo.from_api(pod)

    async def start_pod(
        self,
        name: str,
        *,
        gpu_type_id: str | None = None,
        image_name: str | None = None,
        ports: str = "8188/http,22/tcp",
        container_disk_in_gb: int = 50,
        volume_in_gb: int = 0,
        env: dict[str, str] | None = None,
    ) -> PodInfo:
        """Start a Pod with the configured network volume attached.

        If the primary ``gpu_type_id`` is unavailable (RunPod returns an
        error containing 'gpu' or 'unavailable'), retry once with the
        configured fallback GPU and emit a WARNING log.
        """
        primary = gpu_type_id or self._config.gpu_type_id
        attempts: list[str] = [primary]
        if self._config.gpu_fallback_id and self._config.gpu_fallback_id != primary:
            attempts.append(self._config.gpu_fallback_id)

        last_error: RunpodApiError | None = None
        for index, gpu in enumerate(attempts):
            try:
                return await self._deploy_pod(
                    name=name,
                    gpu_type_id=gpu,
                    image_name=image_name or self._config.docker_image,
                    ports=ports,
                    container_disk_in_gb=container_disk_in_gb,
                    volume_in_gb=volume_in_gb,
                    env=env or {},
                )
            except RunpodApiError as exc:
                last_error = exc
                if index + 1 >= len(attempts):
                    raise
                if not _looks_like_gpu_unavailable(exc):
                    raise
                logger.warning(
                    "Primary GPU '%s' unavailable (%s); retrying with fallback '%s'",
                    gpu,
                    exc,
                    attempts[index + 1],
                )

        # Defensive — loop above either returns or raises, so this is unreachable.
        assert last_error is not None
        raise last_error  # pragma: no cover

    async def _deploy_pod(
        self,
        *,
        name: str,
        gpu_type_id: str,
        image_name: str,
        ports: str,
        container_disk_in_gb: int,
        volume_in_gb: int,
        env: dict[str, str],
    ) -> PodInfo:
        env_payload = [{"key": k, "value": v} for k, v in env.items()]
        variables = {
            "input": {
                "name": name,
                "imageName": image_name,
                "gpuTypeId": gpu_type_id,
                "gpuCount": self._config.gpu_count,
                "ports": ports,
                "containerDiskInGb": container_disk_in_gb,
                "volumeInGb": volume_in_gb,
                "networkVolumeId": self._config.network_volume_id,
                "dataCenterId": self._config.datacenter,
                "env": env_payload,
            }
        }
        if self._config.template_id:
            variables["input"]["templateId"] = self._config.template_id

        query = (
            "mutation DeployOnDemand($input: PodFindAndDeployOnDemandInput!) {"
            "  podFindAndDeployOnDemand(input: $input) {"
            f"    {_POD_FIELDS}"
            "  }"
            "}"
        )
        data = await self._gql(
            query, query_name="podFindAndDeployOnDemand", variables=variables
        )
        pod = data.get("podFindAndDeployOnDemand")
        if not pod:
            raise RunpodApiError(
                "podFindAndDeployOnDemand returned null",
                query_name="podFindAndDeployOnDemand",
            )
        return PodInfo.from_api(pod)

    async def stop_pod(self, pod_id: str) -> bool:
        query = (
            "mutation StopPod($input: PodStopInput!) {"
            "  podStop(input: $input) { id desiredStatus }"
            "}"
        )
        try:
            await self._gql(
                query,
                query_name="podStop",
                variables={"input": {"podId": pod_id}},
            )
        except RunpodApiError as exc:
            if exc.status_code == 404 or _looks_like_not_found(exc):
                logger.info("podStop: pod %s not found, nothing to stop", pod_id)
                return False
            raise
        return True

    async def terminate_pod(self, pod_id: str) -> bool:
        query = (
            "mutation TerminatePod($input: PodTerminateInput!) {"
            "  podTerminate(input: $input)"
            "}"
        )
        try:
            await self._gql(
                query,
                query_name="podTerminate",
                variables={"input": {"podId": pod_id}},
            )
        except RunpodApiError as exc:
            if exc.status_code == 404 or _looks_like_not_found(exc):
                logger.info("podTerminate: pod %s not found", pod_id)
                return False
            raise
        return True

    async def wait_for_ready(
        self, pod_id: str, timeout_sec: int = 300
    ) -> PodInfo:
        """Poll until the pod's ``desiredStatus`` is RUNNING or timeout."""
        deadline = asyncio.get_event_loop().time() + timeout_sec
        delay = 2.0
        last: PodInfo | None = None
        while asyncio.get_event_loop().time() < deadline:
            pod = await self.get_pod(pod_id)
            if pod is not None:
                last = pod
                if (pod.desired_status or "").upper() == "RUNNING":
                    return pod
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 10.0)
        raise RunpodApiError(
            f"pod {pod_id} did not reach RUNNING within {timeout_sec}s "
            f"(last_status={last.desired_status if last else 'unknown'})",
            query_name="wait_for_ready",
        )

    async def get_pod_public_url(
        self, pod_id: str, port: int = 8188
    ) -> str | None:
        pod = await self.get_pod(pod_id)
        if pod is None or not pod.runtime:
            return None
        ports = pod.runtime.get("ports") or []
        for entry in ports:
            if entry.get("privatePort") == port and entry.get("isIpPublic"):
                ip = entry.get("ip")
                public_port = entry.get("publicPort")
                proto = "https" if entry.get("type") == "http" else "http"
                if ip and public_port:
                    return f"{proto}://{ip}:{public_port}"
        return None


def _looks_like_gpu_unavailable(exc: RunpodApiError) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in ("gpu", "no instance", "unavailable", "out of stock", "capacity")
    )


def _looks_like_not_found(exc: RunpodApiError) -> bool:
    text = str(exc).lower()
    return "not found" in text or "no such" in text or "does not exist" in text
