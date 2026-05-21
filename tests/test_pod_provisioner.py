# -*- coding: utf-8 -*-
"""Unit tests for the RunPod pod_provisioner module.

All HTTP and RunPod API traffic is mocked. The provisioner is exercised
through its injectable kwargs (clock, sleeper, http_client, interrupted)
exactly like the sniper's _snipe() — no real time, no real network.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.services.block_m2_video.runpod.pod_provisioner import (
    ProvisionOutcome,
    ProvisionResult,
    wait_for_pod_ready,
)
from app.services.block_m2_video.runpod.runpod_client import (
    PodInfo,
    RunpodClient,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_pod(pod_id: str = "pod_abc123", desired_status: str = "RUNNING") -> PodInfo:
    return PodInfo.from_api(
        {
            "id": pod_id,
            "name": "jarvis-i2v-sniper-1",
            "desiredStatus": desired_status,
            "costPerHr": 1.89,
            "imageName": "runpod/test:latest",
            "machineId": "m_test",
            "gpuCount": 1,
            "lastStatusChange": "now",
            "runtime": {
                "ports": [
                    {
                        "privatePort": 8188,
                        "publicPort": 8188,
                        "ip": "1.2.3.4",
                        "isIpPublic": True,
                        "type": "http",
                    }
                ],
                "uptimeInSeconds": 5,
            },
        }
    )


def _make_response(status_code: int) -> httpx.Response:
    request = httpx.Request("GET", "https://example.proxy.runpod.net/system_stats")
    return httpx.Response(status_code=status_code, request=request)


def _make_http(*responses_or_excs) -> MagicMock:
    """Build a mocked AsyncClient whose .get cycles through the given side_effect."""
    http = MagicMock(spec=httpx.AsyncClient)
    http.get = AsyncMock(side_effect=list(responses_or_excs))
    return http


def _make_clock(*ticks: float):
    """Iterator-backed clock; raises StopIteration if loop runs too long (=bug)."""
    it = iter(ticks)
    return lambda: next(it)


@pytest.fixture
def fake_sleep() -> AsyncMock:
    async def _no_op(_seconds: float) -> None:
        return None

    return AsyncMock(side_effect=_no_op)


# ── tests ─────────────────────────────────────────────────────────────────────


def test_provision_outcome_enum_members():
    assert ProvisionOutcome.READY.value == "ready"
    assert ProvisionOutcome.CONTAINER_EXITED.value == "container_exited"
    assert ProvisionOutcome.TIMEOUT.value == "timeout"


def test_provision_result_is_pydantic_model():
    r = ProvisionResult(
        outcome=ProvisionOutcome.READY,
        pod_id="pod_abc123",
        public_url="https://pod_abc123-8188.proxy.runpod.net",
        elapsed_sec=12.5,
        detail="ready in 12.5s",
    )
    assert r.outcome is ProvisionOutcome.READY
    assert r.pod_id == "pod_abc123"
    assert r.elapsed_sec == 12.5
