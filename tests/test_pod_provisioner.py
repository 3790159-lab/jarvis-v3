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


@pytest.mark.asyncio
async def test_outcome_ready_on_first_poll(fake_sleep: AsyncMock) -> None:
    """First HTTP call returns 200 → READY without sleeping."""
    pod = _make_pod()
    client = AsyncMock(spec=RunpodClient)
    client.get_pod = AsyncMock(return_value=pod)
    http = _make_http(_make_response(200))

    result = await wait_for_pod_ready(
        client,
        pod,
        http_client=http,
        clock=_make_clock(0.0, 0.0, 0.5),  # start, deadline-check, post-success
        sleeper=fake_sleep,
        timeout_min=25,
        poll_interval_sec=30,
    )

    assert result.outcome is ProvisionOutcome.READY
    assert result.pod_id == pod.id
    assert result.public_url == "https://pod_abc123-8188.proxy.runpod.net"
    assert result.elapsed_sec == pytest.approx(0.5, abs=0.01)
    http.get.assert_awaited_once()
    fake_sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_outcome_ready_after_transient_connect_errors(
    fake_sleep: AsyncMock,
) -> None:
    """ConnectError on the first two probes, then 200 on the third → READY."""
    pod = _make_pod()
    client = AsyncMock(spec=RunpodClient)
    client.get_pod = AsyncMock(return_value=pod)
    http = _make_http(
        httpx.ConnectError("not bound yet"),
        httpx.TimeoutException("slow"),
        _make_response(200),
    )

    result = await wait_for_pod_ready(
        client,
        pod,
        http_client=http,
        clock=_make_clock(0.0, 0.0, 30.0, 60.0, 60.5),
        sleeper=fake_sleep,
        timeout_min=25,
        poll_interval_sec=30,
    )

    assert result.outcome is ProvisionOutcome.READY
    assert http.get.await_count == 3
    assert fake_sleep.await_count == 2  # one sleep per failed probe


@pytest.mark.asyncio
async def test_outcome_container_exited_on_pod_exited(
    fake_sleep: AsyncMock,
) -> None:
    """Pod desired_status flips to EXITED → CONTAINER_EXITED, no further waits."""
    running_pod = _make_pod()
    exited_pod = _make_pod(desired_status="EXITED")
    client = AsyncMock(spec=RunpodClient)
    # First get_pod = RUNNING, second = EXITED
    client.get_pod = AsyncMock(side_effect=[running_pod, exited_pod])
    http = _make_http(_make_response(404), _make_response(404))

    result = await wait_for_pod_ready(
        client,
        running_pod,
        http_client=http,
        clock=_make_clock(0.0, 0.0, 30.0, 60.0, 60.5),
        sleeper=fake_sleep,
        timeout_min=25,
        poll_interval_sec=30,
    )

    assert result.outcome is ProvisionOutcome.CONTAINER_EXITED
    assert result.pod_id == running_pod.id
    assert "exited" in result.detail.lower()
    assert running_pod.id in result.detail
    # Returned before deadline — at most a handful of polls
    assert client.get_pod.await_count <= 3


@pytest.mark.asyncio
async def test_container_exited_takes_precedence_over_http_404(
    fake_sleep: AsyncMock,
) -> None:
    """Even if HTTP keeps 404-ing, EXITED state wins in the same iteration."""
    running_pod = _make_pod()
    exited_pod = _make_pod(desired_status="EXITED")
    client = AsyncMock(spec=RunpodClient)
    client.get_pod = AsyncMock(return_value=exited_pod)  # always EXITED
    http = _make_http(_make_response(404))  # one 404, then EXITED wins

    result = await wait_for_pod_ready(
        client,
        running_pod,
        http_client=http,
        clock=_make_clock(0.0, 0.0, 1.0),
        sleeper=fake_sleep,
        timeout_min=25,
        poll_interval_sec=30,
    )

    assert result.outcome is ProvisionOutcome.CONTAINER_EXITED
