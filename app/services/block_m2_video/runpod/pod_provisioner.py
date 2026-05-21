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
    # Placeholder — later tasks build out the real loop.
    return ProvisionResult(
        outcome=ProvisionOutcome.TIMEOUT,
        pod_id=pod.id,
        public_url=None,
        elapsed_sec=0.0,
        detail="not implemented yet",
    )
