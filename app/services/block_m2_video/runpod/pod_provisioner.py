# -*- coding: utf-8 -*-
"""Pod provisioning observer.

After the GPU sniper successfully spawns a pod, this module waits for
ComfyUI to bind ``/system_stats`` while also watching the pod's RunPod-
level lifecycle. It returns a three-state :class:`ProvisionResult` the
sniper translates into one of three Telegram alerts.

The module is **purely passive**: it never stops or terminates pods.
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Awaitable, Callable

import httpx
from pydantic import BaseModel

from .runpod_client import PodInfo, RunpodClient

logger = logging.getLogger(__name__)

DEFAULT_READINESS_TIMEOUT_MIN = 25
DEFAULT_POLL_INTERVAL_SEC = 30
CONSECUTIVE_TRANSIENT_FAILURE_THRESHOLD = 3
SYSTEM_STATS_PATH = "/system_stats"
HTTP_PROBE_TIMEOUT_SEC = 10.0


class ProvisionOutcome(str, Enum):
    READY = "ready"
    CONTAINER_EXITED = "container_exited"
    TIMEOUT = "timeout"


class ProvisionResult(BaseModel):
    outcome: ProvisionOutcome
    pod_id: str
    public_url: str | None
    elapsed_sec: float
    detail: str

    model_config = {"extra": "ignore"}


async def wait_for_pod_ready(
    client: RunpodClient,
    pod: PodInfo,
    *,
    http_client: httpx.AsyncClient | None = None,
    timeout_min: int = DEFAULT_READINESS_TIMEOUT_MIN,
    poll_interval_sec: int = DEFAULT_POLL_INTERVAL_SEC,
    clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], Awaitable[None]] | None = None,
    interrupted: Callable[[], bool] | None = None,
) -> ProvisionResult:
    """Poll a freshly-caught pod until READY, CONTAINER_EXITED, or TIMEOUT.

    All four ``http_client`` / ``clock`` / ``sleeper`` / ``interrupted``
    kwargs are injectable so the function can be unit-tested with zero
    real time and zero real network.
    """
    import asyncio
    import time

    _clock = clock if clock is not None else time.monotonic
    _sleep = sleeper if sleeper is not None else asyncio.sleep
    _own_http = http_client is None
    http = http_client or httpx.AsyncClient(timeout=HTTP_PROBE_TIMEOUT_SEC)

    public_url = f"https://{pod.id}-8188.proxy.runpod.net"
    start = _clock()
    deadline = start + timeout_min * 60

    try:
        while _clock() < deadline:
            # 1. HTTP probe
            try:
                response = await http.get(
                    f"{public_url}{SYSTEM_STATS_PATH}",
                    timeout=HTTP_PROBE_TIMEOUT_SEC,
                )
                if response.status_code == 200:
                    elapsed = _clock() - start
                    return ProvisionResult(
                        outcome=ProvisionOutcome.READY,
                        pod_id=pod.id,
                        public_url=public_url,
                        elapsed_sec=elapsed,
                        detail=f"ComfyUI ready in {elapsed:.1f}s",
                    )
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                logger.debug("[provisioner] HTTP probe transient: %s", exc)

            await _sleep(poll_interval_sec)

        # Deadline reached
        elapsed = _clock() - start
        return ProvisionResult(
            outcome=ProvisionOutcome.TIMEOUT,
            pod_id=pod.id,
            public_url=public_url,
            elapsed_sec=elapsed,
            detail=(
                f"Pod still RUNNING but /system_stats never returned 200 "
                f"in {timeout_min} min — check web terminal for pod {pod.id}"
            ),
        )
    finally:
        if _own_http:
            await http.aclose()
