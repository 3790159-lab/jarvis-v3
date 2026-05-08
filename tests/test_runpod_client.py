# -*- coding: utf-8 -*-
"""Tests for the async RunPod GraphQL client.

All HTTP traffic is mocked via ``unittest.mock.AsyncMock`` — the real
RunPod API is never touched.
"""
from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import SecretStr

from app.services.block_m2_video.runpod.runpod_client import (
    RunpodApiError,
    RunpodClient,
    _mask_key,
)
from app.services.block_m2_video.runpod.runpod_config import RunpodConfig


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_config(**overrides) -> RunpodConfig:
    """Build a :class:`RunpodConfig` without touching real env files."""
    defaults: dict = {
        "RUNPOD_API_KEY": SecretStr("rpa_test_secret_key_abcdef1234"),
        "api_endpoint": "https://api.runpod.io/graphql",
        "RUNPOD_NETWORK_VOLUME_ID": "vol_test_abcdef",
        "datacenter": "EU-TEST-1",
        "RUNPOD_GPU_TYPE_ID": "NVIDIA RTX TEST PRIMARY",
        "gpu_fallback_id": "NVIDIA L40S TEST",
        "gpu_count": 1,
        "template_id": None,
        "RUNPOD_DOCKER_IMAGE": "runpod/test:latest",
        "comfyui_port": 8188,
        "comfyui_auth_token": None,
        "RUNPOD_MAX_BUDGET_USD_PER_DAY": 3.0,
        "RUNPOD_MAX_POD_LIFETIME_MIN": 20,
        "guardian_check_interval_sec": 30,
        "emergency_stop_enabled": True,
    }
    defaults.update(overrides)
    return RunpodConfig.model_construct(**_normalize_defaults(defaults))


def _normalize_defaults(d: dict) -> dict:
    """Collapse alias keys to canonical attribute names for model_construct."""
    alias_map = {
        "RUNPOD_API_KEY": "api_key",
        "RUNPOD_NETWORK_VOLUME_ID": "network_volume_id",
        "RUNPOD_GPU_TYPE_ID": "gpu_type_id",
        "RUNPOD_DOCKER_IMAGE": "docker_image",
        "RUNPOD_MAX_BUDGET_USD_PER_DAY": "max_budget_usd_per_day",
        "RUNPOD_MAX_POD_LIFETIME_MIN": "max_pod_lifetime_min",
    }
    out: dict = {}
    for k, v in d.items():
        out[alias_map.get(k, k)] = v
    return out


def _make_response(json_payload: dict, status_code: int = 200) -> httpx.Response:
    request = httpx.Request("POST", "https://api.runpod.io/graphql")
    return httpx.Response(
        status_code=status_code,
        content=json.dumps(json_payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        request=request,
    )


def _client_with_responses(*responses) -> tuple[RunpodClient, AsyncMock]:
    http = MagicMock(spec=httpx.AsyncClient)
    http.post = AsyncMock(side_effect=list(responses))
    http.aclose = AsyncMock()
    cfg = _make_config()
    client = RunpodClient(config=cfg, http_client=http)
    return client, http.post


# ── unit: helper functions ────────────────────────────────────────────────────


def test_mask_key_long():
    assert _mask_key("rpa_3GB41K7Y0N4YWSG8X3M9RPAGBPHENS47R58UKWOB144ids").startswith(
        "rpa_3G"
    )
    assert _mask_key("rpa_3GB41K7Y0N4YWSG8X3M9RPAGBPHENS47R58UKWOB144ids").endswith(
        "4ids"
    )


def test_mask_key_short():
    assert _mask_key("short") == "***"
    assert _mask_key("") == "***"


# ── account info ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_account_info_returns_email():
    payload = {"data": {"myself": {"id": "u_1", "email": "user@example.com"}}}
    client, post = _client_with_responses(_make_response(payload))

    info = await client.get_account_info()

    assert info == {"id": "u_1", "email": "user@example.com"}
    post.assert_awaited_once()
    call = post.await_args
    assert call.kwargs["headers"]["Authorization"].startswith("Bearer rpa_test")


# ── pod listing ───────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_list_pods_parses_response():
    payload = {
        "data": {
            "myself": {
                "pods": [
                    {
                        "id": "pod_a",
                        "name": "Alpha",
                        "desiredStatus": "RUNNING",
                        "costPerHr": 0.79,
                        "imageName": "runpod/comfy:1",
                        "machineId": "m1",
                        "gpuCount": 1,
                        "lastStatusChange": "2026-05-08T10:00:00Z",
                        "runtime": {"uptimeInSeconds": 120, "ports": []},
                    },
                    {
                        "id": "pod_b",
                        "name": "Beta",
                        "desiredStatus": "EXITED",
                        "costPerHr": 0.0,
                        "imageName": "runpod/comfy:1",
                        "machineId": "m2",
                        "gpuCount": 1,
                        "lastStatusChange": "2026-05-08T08:00:00Z",
                        "runtime": None,
                    },
                ]
            }
        }
    }
    client, _ = _client_with_responses(_make_response(payload))

    pods = await client.list_pods()

    assert len(pods) == 2
    assert pods[0].id == "pod_a"
    assert pods[0].desired_status == "RUNNING"
    assert pods[0].cost_per_hr == 0.79
    assert pods[1].id == "pod_b"
    assert pods[1].desired_status == "EXITED"


# ── start_pod ─────────────────────────────────────────────────────────────────


def _start_pod_response(pod_id: str = "pod_started") -> dict:
    return {
        "data": {
            "podFindAndDeployOnDemand": {
                "id": pod_id,
                "name": "test-pod",
                "desiredStatus": "RUNNING",
                "costPerHr": 0.79,
                "imageName": "runpod/test:latest",
                "machineId": "m_x",
                "gpuCount": 1,
                "lastStatusChange": None,
                "runtime": None,
            }
        }
    }


@pytest.mark.anyio
async def test_start_pod_uses_network_volume_id():
    client, post = _client_with_responses(_make_response(_start_pod_response()))

    pod = await client.start_pod("test-pod")

    assert pod.id == "pod_started"
    body = post.await_args.kwargs["json"]
    variables = body["variables"]["input"]
    assert variables["networkVolumeId"] == "vol_test_abcdef"
    assert variables["dataCenterId"] == "EU-TEST-1"
    assert variables["gpuTypeId"] == "NVIDIA RTX TEST PRIMARY"
    assert variables["imageName"] == "runpod/test:latest"
    assert variables["gpuCount"] == 1


@pytest.mark.anyio
async def test_start_pod_falls_back_on_gpu_unavailable(caplog):
    error_payload = {
        "errors": [{"message": "no gpus available for type NVIDIA RTX TEST PRIMARY"}]
    }
    success_payload = _start_pod_response("pod_fallback")
    client, post = _client_with_responses(
        _make_response(error_payload, status_code=200),
        _make_response(success_payload),
    )

    with caplog.at_level(logging.WARNING):
        pod = await client.start_pod("test-pod")

    assert pod.id == "pod_fallback"
    assert post.await_count == 2

    first = post.await_args_list[0].kwargs["json"]["variables"]["input"]
    second = post.await_args_list[1].kwargs["json"]["variables"]["input"]
    assert first["gpuTypeId"] == "NVIDIA RTX TEST PRIMARY"
    assert second["gpuTypeId"] == "NVIDIA L40S TEST"
    # Volume must still be attached on the fallback attempt.
    assert second["networkVolumeId"] == "vol_test_abcdef"

    fallback_warnings = [
        r for r in caplog.records if "fallback" in r.getMessage().lower()
    ]
    assert fallback_warnings, "expected a fallback WARNING log line"


# ── stop_pod ──────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_stop_pod_returns_false_on_404():
    not_found = {"errors": [{"message": "pod not found"}]}
    client, _ = _client_with_responses(
        _make_response(not_found, status_code=404),
    )

    ok = await client.stop_pod("missing_pod")

    assert ok is False


@pytest.mark.anyio
async def test_stop_pod_returns_true_on_success():
    payload = {"data": {"podStop": {"id": "pod_a", "desiredStatus": "EXITED"}}}
    client, _ = _client_with_responses(_make_response(payload))

    ok = await client.stop_pod("pod_a")

    assert ok is True


# ── logging masks the API key ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_api_key_masked_in_logs(caplog):
    payload = {"data": {"myself": {"id": "u", "email": "x@y"}}}
    client, _ = _client_with_responses(_make_response(payload))

    with caplog.at_level(logging.DEBUG, logger="app.services.block_m2_video.runpod"):
        await client.get_account_info()

    full_key = "rpa_test_secret_key_abcdef1234"
    masked = _mask_key(full_key)
    debug_text = "\n".join(
        r.getMessage()
        for r in caplog.records
        if r.name.startswith("app.services.block_m2_video.runpod")
    )

    assert masked in debug_text, f"expected masked key in logs, got:\n{debug_text}"
    assert full_key not in debug_text, "raw API key leaked into logs"
