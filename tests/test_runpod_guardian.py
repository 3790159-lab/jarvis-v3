# -*- coding: utf-8 -*-
"""Tests for the RunPod guardian background worker.

Mocks :class:`RunpodClient` entirely; no HTTP traffic is generated.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr

from app.services.block_m2_video.runpod.runpod_client import PodInfo, RunpodClient
from app.services.block_m2_video.runpod.runpod_config import RunpodConfig
from app.services.block_m2_video.runpod.runpod_guardian import (
    CheckResult,
    RunpodGuardian,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_config(**overrides) -> RunpodConfig:
    defaults = {
        "api_key": SecretStr("rpa_test_key_abcdef1234567890"),
        "api_endpoint": "https://api.runpod.io/graphql",
        "network_volume_id": "vol_x",
        "datacenter": "EU-TEST-1",
        "gpu_type_id": "GPU-PRIMARY",
        "gpu_fallback_id": "GPU-FALLBACK",
        "gpu_count": 1,
        "template_id": None,
        "docker_image": "runpod/test:latest",
        "comfyui_port": 8188,
        "comfyui_auth_token": None,
        "max_budget_usd_per_day": 3.0,
        "max_pod_lifetime_min": 20,
        "guardian_check_interval_sec": 30,
        "emergency_stop_enabled": True,
    }
    defaults.update(overrides)
    return RunpodConfig.model_construct(**defaults)


def _make_pod(
    pod_id: str,
    *,
    status: str = "RUNNING",
    uptime_sec: int | None = 60,
) -> PodInfo:
    runtime = {"uptimeInSeconds": uptime_sec, "ports": []} if uptime_sec is not None else None
    return PodInfo.model_construct(
        id=pod_id,
        name=pod_id,
        desired_status=status,
        cost_per_hr=0.79,
        image_name="runpod/test:latest",
        machine_id="m_x",
        gpu_count=1,
        last_status_change=None,
        runtime=runtime,
        raw=None,
    )


def _mock_client(*, pods: list[PodInfo]) -> MagicMock:
    client = MagicMock(spec=RunpodClient)
    client.list_pods = AsyncMock(return_value=pods)
    client.stop_pod = AsyncMock(return_value=True)
    client.terminate_pod = AsyncMock(return_value=True)
    return client


# ── lifetime enforcement ──────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_check_once_stops_overlong_pod(tmp_path):
    cfg = _make_config(max_pod_lifetime_min=10)  # 600s
    pod = _make_pod("pod_old", status="RUNNING", uptime_sec=900)
    client = _mock_client(pods=[pod])
    guardian = RunpodGuardian(client=client, config=cfg, state_dir=tmp_path)

    result = await guardian.check_once()

    client.stop_pod.assert_awaited_once_with("pod_old")
    assert isinstance(result, CheckResult)
    assert result.stopped_for_lifetime == ["pod_old"]
    assert result.pods_inspected == 1
    assert result.budget_exceeded is False


@pytest.mark.anyio
async def test_check_once_skips_fresh_pods(tmp_path):
    cfg = _make_config(max_pod_lifetime_min=20)  # 1200s
    pod = _make_pod("pod_fresh", status="RUNNING", uptime_sec=60)
    client = _mock_client(pods=[pod])
    guardian = RunpodGuardian(client=client, config=cfg, state_dir=tmp_path)

    result = await guardian.check_once()

    client.stop_pod.assert_not_awaited()
    assert result.stopped_for_lifetime == []


# ── budget enforcement ───────────────────────────────────────────────────────


def _write_billing(state_dir, *, today_total: float) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    line = json.dumps(
        {"ts": f"{today}T12:00:00+00:00", "cost_usd": today_total}
    )
    (state_dir / "billing.jsonl").write_text(line + "\n", encoding="utf-8")


@pytest.mark.anyio
async def test_check_once_triggers_emergency_on_budget_exceeded(tmp_path):
    cfg = _make_config(max_budget_usd_per_day=2.0, max_pod_lifetime_min=999999)
    _write_billing(tmp_path, today_total=5.0)

    pods = [
        _make_pod("p1", status="RUNNING", uptime_sec=10),
        _make_pod("p2", status="RUNNING", uptime_sec=10),
        _make_pod("p3", status="EXITED", uptime_sec=None),
    ]
    client = _mock_client(pods=pods)
    guardian = RunpodGuardian(client=client, config=cfg, state_dir=tmp_path)

    result = await guardian.check_once()

    assert result.budget_exceeded is True
    assert sorted(result.emergency_stopped) == ["p1", "p2"]
    # EXITED pod must NOT be stopped.
    stopped_ids = [c.args[0] for c in client.stop_pod.await_args_list]
    assert sorted(stopped_ids) == ["p1", "p2"]


@pytest.mark.anyio
async def test_emergency_stop_all_returns_stopped_ids(tmp_path):
    cfg = _make_config()
    pods = [
        _make_pod("p1"),
        _make_pod("p2"),
        _make_pod("p3"),
    ]
    client = _mock_client(pods=pods)
    guardian = RunpodGuardian(client=client, config=cfg, state_dir=tmp_path)

    stopped = await guardian.emergency_stop_all("manual test")

    assert sorted(stopped) == ["p1", "p2", "p3"]
    assert client.stop_pod.await_count == 3


@pytest.mark.anyio
async def test_guardian_writes_to_jsonl(tmp_path):
    cfg = _make_config(max_pod_lifetime_min=1)  # 60s
    pod = _make_pod("pod_old", status="RUNNING", uptime_sec=120)
    client = _mock_client(pods=[pod])
    guardian = RunpodGuardian(client=client, config=cfg, state_dir=tmp_path)

    await guardian.check_once()

    log_path = tmp_path / "guardian.jsonl"
    assert log_path.exists(), "guardian.jsonl must be created"

    lines = [
        line for line in log_path.read_text(encoding="utf-8").splitlines() if line
    ]
    assert lines, "expected at least one event in guardian.jsonl"

    parsed = [json.loads(line) for line in lines]
    events = {entry["event"] for entry in parsed}
    assert "stopped_for_lifetime" in events
    lifetime_entry = next(
        e for e in parsed if e["event"] == "stopped_for_lifetime"
    )
    assert lifetime_entry["pod_id"] == "pod_old"
    assert lifetime_entry["stopped_ok"] is True
