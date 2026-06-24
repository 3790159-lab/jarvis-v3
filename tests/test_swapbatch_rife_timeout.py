# -*- coding: utf-8 -*-
"""RIFE delivery-stall fix: per-video timeout + parallel isolation.

RIFE is an OPTIONAL polish — it must NEVER degrade base-video delivery. A
single hung interpolation must fail fast (timeout) and hand back THAT video's
un-smoothed original, in parallel, without holding the rest of the batch
hostage. Money-safe billing (only successfully-smoothed videos charged) is
preserved.
"""
import asyncio
import time
from pathlib import Path

import pytest

from app.handlers.face_swap_handler import FaceSwapHandler
from app.services.audit import cost_tracker as _cost


class _FakeRife:
    """Smooths vid_N.mp4 -> vid_N.smooth.mp4. Indices in ``hang`` sleep past the
    timeout (so wait_for must cancel them). Tracks in-flight count (for the
    Semaphore cap) and which indices were actually CancelledError'd (proving the
    coroutine — and its underlying httpx request — is torn down, not leaked)."""

    def __init__(self, hang=(), delay=0.0, hang_sleep=3.0):
        self.hang = set(hang)
        self.delay = delay
        self.hang_sleep = hang_sleep
        self.inflight = 0
        self.max_inflight = 0
        self.cancelled = []

    async def interpolate(self, video, *, num_frames=1):
        assert num_frames == 1
        idx = int(Path(video).stem.split("_")[-1])
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            if idx in self.hang:
                try:
                    await asyncio.sleep(self.hang_sleep)
                except asyncio.CancelledError:
                    self.cancelled.append(idx)
                    raise
            elif self.delay:
                await asyncio.sleep(self.delay)
            return Path(video).with_name(f"{Path(video).stem}.smooth.mp4")
        finally:
            self.inflight -= 1


@pytest.fixture
def recorded(monkeypatch):
    calls = []
    monkeypatch.setattr(_cost, "record_cost",
                        lambda uid, uname, amt: calls.append(amt))
    return calls


def _handler(monkeypatch, fake):
    h = FaceSwapHandler(orchestrator=None)
    monkeypatch.setattr(h, "_make_rife_client", lambda: fake)
    return h


def _vids(tmp_path, n):
    out = []
    for i in range(n):
        p = tmp_path / f"vid_{i}.mp4"
        p.write_bytes(b"x")
        out.append(p)
    return out


@pytest.mark.asyncio
async def test_hung_video_times_out_to_original(tmp_path, recorded, monkeypatch):
    monkeypatch.delenv("SWAPBATCH_RIFE_USD_PER_SEC", raising=False)
    monkeypatch.setenv("SWAPBATCH_RIFE_TIMEOUT_SEC", "0.3")
    fake = _FakeRife(hang={1})
    h = _handler(monkeypatch, fake)
    vids = _vids(tmp_path, 2)

    out = await h._interpolate_batch(vids, 10, user_id=7, username="u")

    assert out[0].name == "vid_0.smooth.mp4"   # fast one smoothed
    assert out[1] == vids[1]                    # hung one → original, not dropped
    assert recorded == [pytest.approx(1 * 10 * 0.01)]   # billed only the smoothed
    assert 1 in fake.cancelled                  # wait_for actually CANCELLED it
    assert fake.inflight == 0                   # no coroutine left lingering


@pytest.mark.asyncio
async def test_two_hung_run_in_parallel_not_serial(tmp_path, recorded, monkeypatch):
    monkeypatch.setenv("SWAPBATCH_RIFE_TIMEOUT_SEC", "0.5")
    monkeypatch.setenv("SWAPBATCH_ANIMATE_CONCURRENCY", "2")
    fake = _FakeRife(hang={0, 1})
    h = _handler(monkeypatch, fake)
    vids = _vids(tmp_path, 2)

    t = time.monotonic()
    out = await h._interpolate_batch(vids, 5, user_id=7, username="u")
    elapsed = time.monotonic() - t

    assert out == vids                  # both originals
    assert recorded == []               # nothing smoothed → zero RIFE charge
    # parallel: both time out together (~0.5s), NOT serially (~1.0s+)
    assert elapsed < 0.9


@pytest.mark.asyncio
async def test_order_preserved_with_mixed_results(tmp_path, recorded, monkeypatch):
    # gather keeps input order: delivered[i] must correspond to videos[i].
    monkeypatch.setenv("SWAPBATCH_RIFE_TIMEOUT_SEC", "0.3")
    fake = _FakeRife(hang={1})
    h = _handler(monkeypatch, fake)
    vids = _vids(tmp_path, 3)

    out = await h._interpolate_batch(vids, 5, user_id=7, username="u")

    assert out[0].name == "vid_0.smooth.mp4"
    assert out[1] == vids[1]            # hung middle → its own original (no drift)
    assert out[2].name == "vid_2.smooth.mp4"


@pytest.mark.asyncio
async def test_concurrency_cap_respected(tmp_path, recorded, monkeypatch):
    monkeypatch.setenv("SWAPBATCH_ANIMATE_CONCURRENCY", "2")
    monkeypatch.setenv("SWAPBATCH_RIFE_TIMEOUT_SEC", "5")
    fake = _FakeRife(delay=0.2)        # all succeed, but take time → overlap
    h = _handler(monkeypatch, fake)
    vids = _vids(tmp_path, 6)

    out = await h._interpolate_batch(vids, 5, user_id=7, username="u")

    assert all(v.name.endswith(".smooth.mp4") for v in out)
    assert fake.max_inflight <= 2      # Semaphore never lets more than cap run


@pytest.mark.asyncio
async def test_all_success_regression(tmp_path, recorded, monkeypatch):
    monkeypatch.delenv("SWAPBATCH_RIFE_USD_PER_SEC", raising=False)
    fake = _FakeRife()                  # no env timeout set → default 90 benign
    h = _handler(monkeypatch, fake)
    vids = _vids(tmp_path, 2)

    out = await h._interpolate_batch(vids, 10, user_id=7, username="u")

    assert [v.name for v in out] == ["vid_0.smooth.mp4", "vid_1.smooth.mp4"]
    assert recorded == [pytest.approx(2 * 10 * 0.01)]   # both billed


@pytest.mark.asyncio
async def test_client_construction_failure_delivers_originals(tmp_path, recorded, monkeypatch):
    h = FaceSwapHandler(orchestrator=None)

    def _boom():
        raise RuntimeError("WAVESPEED_API_KEY not set")
    monkeypatch.setattr(h, "_make_rife_client", _boom)
    vids = _vids(tmp_path, 2)

    out = await h._interpolate_batch(vids, 10, user_id=7, username="u")

    assert out == vids
    assert recorded == []
