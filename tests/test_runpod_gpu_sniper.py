# -*- coding: utf-8 -*-
"""Unit tests for the RunPod GPU sniper.

The sniper itself is tested via its injectable :func:`_snipe` core; no real
HTTP traffic and no real ``asyncio.sleep`` waits — the sleep helper and
monotonic clock are stubbed.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from app.services.block_m2_video.runpod.runpod_client import (
    PodInfo,
    RunpodApiError,
    RunpodSupplyError,
)
from app.services.block_m2_video.runpod.runpod_config import RunpodConfig
from app.services.block_m2_video.runpod.pod_provisioner import (
    ProvisionOutcome,
    ProvisionResult,
)
from scripts import runpod_gpu_sniper as sniper


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_config() -> RunpodConfig:
    """Build a :class:`RunpodConfig` without touching real env files."""
    return RunpodConfig.model_construct(
        api_key=SecretStr("rpa_test_secret_key_abcdef1234"),
        api_endpoint="https://api.runpod.io/graphql",
        network_volume_id="vol_test",
        datacenter="EU-RO-1",
        gpu_type_id="NVIDIA A100 80GB PCIe",
        gpu_fallback_id="NVIDIA A100-SXM4-80GB",
        gpu_count=1,
        template_id=None,
        docker_image="runpod/test:latest",
        comfyui_port=8188,
        comfyui_auth_token=None,
        max_budget_usd_per_day=5.0,
        max_pod_lifetime_min=120,
        guardian_check_interval_sec=30,
        emergency_stop_enabled=True,
    )


def _make_pod(pod_id: str = "pod_abc123") -> PodInfo:
    return PodInfo.from_api(
        {
            "id": pod_id,
            "name": "jarvis-i2v-sniper-1",
            "desiredStatus": "RUNNING",
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


@pytest.fixture(autouse=True)
def _no_real_notify(monkeypatch: pytest.MonkeyPatch) -> None:
    """Don't ever hit Telegram from these tests, even if env happens to be set."""
    monkeypatch.setattr(sniper, "send_alert", lambda _text: True)


@pytest.fixture(autouse=True)
def _no_real_provisioner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub wait_for_pod_ready so existing tests don't block on HTTP/RunPod polls."""

    async def _instant_ready(_client: Any, _pod: Any, **_kw: Any) -> Any:
        from app.services.block_m2_video.runpod.pod_provisioner import (
            ProvisionOutcome,
            ProvisionResult,
        )

        return ProvisionResult(
            outcome=ProvisionOutcome.READY,
            pod_id=_pod.id,
            public_url=f"https://{_pod.id}-8188.proxy.runpod.net",
            elapsed_sec=0.0,
            detail="stub",
        )

    monkeypatch.setattr(sniper, "wait_for_pod_ready", _instant_ready)


@pytest.fixture
def status_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "state" / "runpod_sniper_status.json"
    monkeypatch.setattr(sniper, "STATUS_PATH", target)
    return target


@pytest.fixture
def fake_sleep() -> AsyncMock:
    async def _no_op(_seconds: float) -> None:
        return None

    return AsyncMock(side_effect=_no_op)


def _read_status(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_immediate_success(status_file: Path, fake_sleep: AsyncMock) -> None:
    """start_pod returns a pod on the first attempt → status caught."""
    pod = _make_pod()
    client = AsyncMock()
    client.start_pod = AsyncMock(return_value=pod)

    rc = await sniper._snipe(
        max_duration_min=120,
        poll_interval_sec=20,
        notify=False,
        dry_run=False,
        config=_make_config(),
        client=client,
        sleeper=fake_sleep,
    )

    assert rc == 0
    assert client.start_pod.await_count == 1
    fake_sleep.assert_not_awaited()

    status = _read_status(status_file)
    assert status["status"] == "caught"
    assert status["pod_id"] == pod.id
    assert status["pod_public_url"] == f"https://{pod.id}-8188.proxy.runpod.net"
    assert status["attempts"] == 1


@pytest.mark.asyncio
async def test_retries_on_supply_error(
    status_file: Path, fake_sleep: AsyncMock
) -> None:
    """First 3 calls raise RunpodSupplyError, 4th succeeds → attempts=4."""
    pod = _make_pod("pod_caught_on_4th")
    client = AsyncMock()
    client.start_pod = AsyncMock(
        side_effect=[
            RunpodSupplyError("supply_constraint round 1"),
            RunpodSupplyError("supply_constraint round 2"),
            RunpodSupplyError("supply_constraint round 3"),
            pod,
        ]
    )

    rc = await sniper._snipe(
        max_duration_min=120,
        poll_interval_sec=20,
        notify=False,
        dry_run=False,
        config=_make_config(),
        client=client,
        sleeper=fake_sleep,
    )

    assert rc == 0
    assert client.start_pod.await_count == 4
    assert fake_sleep.await_count == 3  # one sleep per supply-empty cycle

    status = _read_status(status_file)
    assert status["status"] == "caught"
    assert status["pod_id"] == "pod_caught_on_4th"
    assert status["attempts"] == 4


@pytest.mark.asyncio
async def test_exits_on_api_error(
    status_file: Path, fake_sleep: AsyncMock
) -> None:
    """Non-supply RunpodApiError → status 'error', clean exit, no retry."""
    client = AsyncMock()
    client.start_pod = AsyncMock(
        side_effect=RunpodApiError("HTTP 401: bad api key", status_code=401)
    )

    rc = await sniper._snipe(
        max_duration_min=120,
        poll_interval_sec=20,
        notify=False,
        dry_run=False,
        config=_make_config(),
        client=client,
        sleeper=fake_sleep,
    )

    assert rc == 1
    assert client.start_pod.await_count == 1
    fake_sleep.assert_not_awaited()

    status = _read_status(status_file)
    assert status["status"] == "error"
    assert "HTTP 401" in status["error_detail"]
    assert status["pod_id"] is None


@pytest.mark.asyncio
async def test_respects_max_duration(
    status_file: Path, fake_sleep: AsyncMock
) -> None:
    """Supply stays empty past the deadline → status 'timeout'."""
    client = AsyncMock()
    client.start_pod = AsyncMock(
        side_effect=RunpodSupplyError("supply_constraint forever")
    )

    # Fake monotonic clock: jumps by 60s every call. With max=2min the loop
    # should bail out after roughly 2 attempts (deadline hits on 3rd check).
    ticks = iter([0.0, 60.0, 120.0, 180.0, 240.0, 300.0, 360.0])

    def _clock() -> float:
        return next(ticks)

    rc = await sniper._snipe(
        max_duration_min=2,
        poll_interval_sec=20,
        notify=False,
        dry_run=False,
        config=_make_config(),
        client=client,
        sleeper=fake_sleep,
        clock=_clock,
    )

    assert rc == 2
    # Sniper should give up before infinite retries
    assert client.start_pod.await_count <= 3

    status = _read_status(status_file)
    assert status["status"] == "timeout"
    assert "deadline" in (status["error_detail"] or "").lower()


@pytest.mark.asyncio
async def test_dry_run_no_spawn(
    status_file: Path, fake_sleep: AsyncMock
) -> None:
    """--dry-run path: no start_pod calls, status flips to caught with sentinel id."""
    client = AsyncMock()
    client.start_pod = AsyncMock()  # should NEVER be called in dry-run

    rc = await sniper._snipe(
        max_duration_min=120,
        poll_interval_sec=20,
        notify=False,
        dry_run=True,
        config=_make_config(),
        client=client,
        sleeper=fake_sleep,
    )

    assert rc == 0
    client.start_pod.assert_not_awaited()
    fake_sleep.assert_not_awaited()

    status = _read_status(status_file)
    assert status["status"] == "caught"
    assert status["pod_id"] == "DRY-RUN-NO-POD"
    assert status["config"]["dry_run"] is True


@pytest.mark.asyncio
async def test_calls_provisioner_after_catch_and_alerts_ready(
    status_file: Path,
    fake_sleep: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After catch, sniper calls wait_for_pod_ready and sends a ✅ Pod ready alert."""
    pod = _make_pod()
    client = AsyncMock()
    client.start_pod = AsyncMock(return_value=pod)

    sent: list[str] = []
    monkeypatch.setattr(sniper, "send_alert", lambda text: sent.append(text) or True)

    async def _fake_wait_for_pod_ready(_client, _pod, **_kw):
        return ProvisionResult(
            outcome=ProvisionOutcome.READY,
            pod_id=_pod.id,
            public_url=f"https://{_pod.id}-8188.proxy.runpod.net",
            elapsed_sec=42.0,
            detail="ComfyUI ready in 42.0s",
        )

    monkeypatch.setattr(sniper, "wait_for_pod_ready", _fake_wait_for_pod_ready)

    rc = await sniper._snipe(
        max_duration_min=120,
        poll_interval_sec=20,
        notify=True,
        dry_run=False,
        config=_make_config(),
        client=client,
        sleeper=fake_sleep,
    )

    assert rc == 0
    # Two alerts: catch + ready
    assert len(sent) == 2
    assert "🎯" in sent[0]
    assert "waiting for ComfyUI bootstrap" in sent[0]
    assert "✅" in sent[1]
    assert "ready" in sent[1].lower()
    assert pod.id in sent[1]


@pytest.mark.asyncio
async def test_alerts_container_exited(
    status_file: Path,
    fake_sleep: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CONTAINER_EXITED outcome → ❌ alert mentioning the pod_id."""
    pod = _make_pod()
    client = AsyncMock()
    client.start_pod = AsyncMock(return_value=pod)

    sent: list[str] = []
    monkeypatch.setattr(sniper, "send_alert", lambda text: sent.append(text) or True)

    async def _fake_wait(_c, _p, **_kw):
        return ProvisionResult(
            outcome=ProvisionOutcome.CONTAINER_EXITED,
            pod_id=_p.id,
            public_url=None,
            elapsed_sec=45.0,
            detail=f"Container exited 45s after spawn — check RunPod console logs for pod {_p.id}",
        )

    monkeypatch.setattr(sniper, "wait_for_pod_ready", _fake_wait)

    rc = await sniper._snipe(
        max_duration_min=120,
        poll_interval_sec=20,
        notify=True,
        dry_run=False,
        config=_make_config(),
        client=client,
        sleeper=fake_sleep,
    )

    assert rc == 0  # catch succeeded; provisioning failure is not a sniper-level error
    assert len(sent) == 2
    assert "❌" in sent[1]
    assert pod.id in sent[1]


@pytest.mark.asyncio
async def test_alerts_timeout(
    status_file: Path,
    fake_sleep: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TIMEOUT outcome → ⏰ alert mentioning the pod_id."""
    pod = _make_pod()
    client = AsyncMock()
    client.start_pod = AsyncMock(return_value=pod)

    sent: list[str] = []
    monkeypatch.setattr(sniper, "send_alert", lambda text: sent.append(text) or True)

    async def _fake_wait(_c, _p, **_kw):
        return ProvisionResult(
            outcome=ProvisionOutcome.TIMEOUT,
            pod_id=_p.id,
            public_url=f"https://{_p.id}-8188.proxy.runpod.net",
            elapsed_sec=1500.0,
            detail=f"Pod still RUNNING but /system_stats never returned 200 in 25 min — check web terminal for pod {_p.id}",
        )

    monkeypatch.setattr(sniper, "wait_for_pod_ready", _fake_wait)

    rc = await sniper._snipe(
        max_duration_min=120,
        poll_interval_sec=20,
        notify=True,
        dry_run=False,
        config=_make_config(),
        client=client,
        sleeper=fake_sleep,
    )

    assert rc == 0
    assert len(sent) == 2
    assert "⏰" in sent[1]
    assert pod.id in sent[1]


@pytest.mark.asyncio
async def test_sigint_during_readiness_sends_aborted_alert(
    status_file: Path,
    fake_sleep: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SIGINT during wait_for_pod_ready → 🛑 'readiness aborted' alert, pod left alive."""
    pod = _make_pod()
    client = AsyncMock()
    client.start_pod = AsyncMock(return_value=pod)
    # The sniper must NOT call terminate_pod
    client.terminate_pod = AsyncMock()

    sent: list[str] = []
    monkeypatch.setattr(sniper, "send_alert", lambda text: sent.append(text) or True)

    async def _fake_wait(_c, _p, **_kw):
        # Simulate the provisioner returning TIMEOUT with the SIGINT marker
        # — the sniper differentiates by detail content, not by outcome.
        return ProvisionResult(
            outcome=ProvisionOutcome.TIMEOUT,
            pod_id=_p.id,
            public_url=f"https://{_p.id}-8188.proxy.runpod.net",
            elapsed_sec=120.0,
            detail=f"SIGINT during readiness wait for {_p.id}",
        )

    monkeypatch.setattr(sniper, "wait_for_pod_ready", _fake_wait)

    rc = await sniper._snipe(
        max_duration_min=120,
        poll_interval_sec=20,
        notify=True,
        dry_run=False,
        config=_make_config(),
        client=client,
        sleeper=fake_sleep,
    )

    assert rc == 0
    assert len(sent) == 2
    assert "🛑" in sent[1]
    assert "aborted" in sent[1].lower()
    assert pod.id in sent[1]
    client.terminate_pod.assert_not_awaited()
