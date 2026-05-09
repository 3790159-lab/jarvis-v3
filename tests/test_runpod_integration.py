# -*- coding: utf-8 -*-
"""Real RunPod API integration tests.

These tests hit the live RunPod GraphQL endpoint with the project's
configured API key. They are read-only — no pods are started, terminated,
or modified — so they should not incur charges. Disabled by default; opt
in by setting ``RUNPOD_INTEGRATION_TESTS=1``.
"""
from __future__ import annotations

import os

import pytest

from app.services.block_m2_video.runpod.runpod_client import RunpodClient
from app.services.block_m2_video.runpod.runpod_config import get_runpod_config

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("RUNPOD_INTEGRATION_TESTS"),
        reason="set RUNPOD_INTEGRATION_TESTS=1 to enable",
    ),
]


@pytest.fixture
async def real_client():
    cfg = get_runpod_config()
    client = RunpodClient(config=cfg)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_real_get_account_info(real_client):
    info = await real_client.get_account_info()
    assert info.get("email"), "expected an email on RunPod account"
    assert info.get("id"), "expected an account id"


@pytest.mark.anyio
async def test_real_list_gpu_types_includes_configured(real_client):
    cfg = get_runpod_config()
    gpus = await real_client.list_gpu_types()
    ids = {gpu.id for gpu in gpus} | {gpu.display_name for gpu in gpus}
    assert cfg.gpu_type_id in ids, (
        f"configured GPU '{cfg.gpu_type_id}' not in RunPod catalog "
        f"(have {len(gpus)} entries)"
    )


@pytest.mark.anyio
async def test_real_list_pods_succeeds(real_client):
    # The result can be empty — we only care that the call returns
    # without raising.
    pods = await real_client.list_pods()
    assert isinstance(pods, list)


@pytest.mark.anyio
async def test_real_network_volume_exists(real_client):
    """Confirm the configured network volume id is visible to our account.

    RunPod exposes volumes via ``myself { networkVolumes { id ... } }``;
    we query that directly here so the test does not depend on whatever
    public catalog endpoints might exist.
    """
    cfg = get_runpod_config()
    query = (
        "query MyVolumes { myself { networkVolumes { id name dataCenterId size } } }"
    )
    data = await real_client._gql(query, query_name="MyVolumes")  # noqa: SLF001
    volumes = (data.get("myself") or {}).get("networkVolumes") or []
    ids = {v.get("id") for v in volumes if isinstance(v, dict)}
    assert cfg.network_volume_id in ids, (
        f"configured volume '{cfg.network_volume_id}' not visible in "
        f"account network volumes (have {sorted(ids)})"
    )
