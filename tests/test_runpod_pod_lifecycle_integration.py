# -*- coding: utf-8 -*-
"""Phase 3.0 real-pod lifecycle test.

Double-gated: this test really starts a Pod and pays for it. It will only
run when both ``RUNPOD_INTEGRATION_TESTS=1`` AND ``RUNPOD_REAL_POD_TESTS=1``
are set in the environment. The pod is **always** stopped in a finally
block, even if assertions fail.
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone

import pytest

from app.services.block_m2_video.runpod.runpod_client import (
    GpuType,
    RunpodClient,
)
from app.services.block_m2_video.runpod.runpod_config import get_runpod_config

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (
            os.getenv("RUNPOD_INTEGRATION_TESTS")
            and os.getenv("RUNPOD_REAL_POD_TESTS")
        ),
        reason=(
            "set RUNPOD_INTEGRATION_TESTS=1 and RUNPOD_REAL_POD_TESTS=1 "
            "to enable (this spends real money)"
        ),
    ),
]


def _gpu_price(gpu: GpuType) -> float | None:
    candidates = [
        p for p in (gpu.community_price, gpu.secure_price) if p and p > 0
    ]
    return min(candidates) if candidates else None


def _pick_cheapest(gpus: list[GpuType]) -> GpuType:
    priced = [(gpu, _gpu_price(gpu)) for gpu in gpus]
    priced = [(gpu, price) for gpu, price in priced if price is not None]
    assert priced, "no priced GPU offerings available"
    priced.sort(key=lambda item: item[1])
    return priced[0][0]


@pytest.mark.anyio
async def test_full_pod_lifecycle():
    """Start a real Pod, confirm RUNNING, then verify our stop_pod works."""
    config = get_runpod_config()
    client = RunpodClient(config=config)
    pod = None
    started_at = time.time()
    try:
        gpus = await client.list_gpu_types()
        chosen = _pick_cheapest(gpus)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        pod = await client.start_pod(
            name=f"jarvis-test-lifecycle-{timestamp}",
            gpu_type_id=chosen.id,
            image_name=config.docker_image,
            ports="22/tcp,8888/http",
            container_disk_in_gb=5,
            volume_in_gb=0,
        )
        assert pod.id, "start_pod must return a pod id"

        ready = await client.wait_for_ready(pod.id, timeout_sec=180)
        assert (ready.desired_status or "").upper() == "RUNNING"

    finally:
        if pod is not None:
            stopped = await client.stop_pod(pod.id)
            assert stopped is True, "stop_pod must succeed for a running pod"

            # Give RunPod a few seconds to update the pod status.
            await asyncio.sleep(5)
            final = await client.get_pod(pod.id)
            assert (
                final is None
                or (final.desired_status or "").upper()
                in {"EXITED", "TERMINATED", "STOPPED"}
            ), (
                f"pod {pod.id} did not reach a stopped state "
                f"(got {final.desired_status if final else 'NOT_FOUND'})"
            )

        elapsed = time.time() - started_at
        # Soft sanity check — the whole test should complete inside the
        # guardian's lifetime ceiling (default 20 min).
        assert elapsed < config.max_pod_lifetime_min * 60, (
            f"lifecycle test ran for {elapsed:.0f}s, exceeding the configured "
            f"max_pod_lifetime_min ({config.max_pod_lifetime_min} min)"
        )

        await client.aclose()
