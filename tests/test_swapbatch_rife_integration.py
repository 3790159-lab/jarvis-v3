# -*- coding: utf-8 -*-
"""Задача 5 (TDD): RIFE interpolation wired into the batch pipeline.

The money-safe contract under test:
  * interpolation runs ONLY when the batch session has smooth_enabled=True;
  * it runs AFTER the animate billing (so the order of charges is animate→RIFE);
  * a per-video RIFE failure delivers that video's UN-smoothed original instead
    of dropping it — the user always receives a video;
  * billing counts ONLY videos that were actually smoothed (a failed/un-hosted
    video is never charged for RIFE).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.handlers import face_swap_handler as fsh
from app.handlers.face_swap_handler import FaceSwapHandler
from app.services.audit import cost_tracker as _cost
from app.services.block_m2_face_swap.batch_orchestrator import (
    BatchOrchestrator,
    STATE_SWAP_DONE,
)
from app.services.block_m2_video.engines.wavespeed_http import WaveSpeedEngineError


# ── helpers ────────────────────────────────────────────────────────────────────


def _mk(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
    return p


def _handler(tmp_path):
    orch = BatchOrchestrator(state_root=tmp_path / "b", validator=_FakeValidator())
    return FaceSwapHandler(orchestrator=orch), orch


class _FakeValidator:
    def count_faces(self, p):
        return 1


class _FakeRife:
    """Records each interpolate() call; fails on paths whose index is in fail."""

    def __init__(self, fail: set[int] | None = None):
        self.fail = fail or set()
        self.calls: list[Path] = []

    async def interpolate(self, video: Path, *, num_frames: int = 1) -> Path:
        idx = len(self.calls)
        self.calls.append(video)
        assert num_frames == 1
        if idx in self.fail:
            raise WaveSpeedEngineError("RIFE boom")
        return Path(video).with_name(f"{Path(video).stem}.smooth.mp4")


def _seed_animate_ready(handler, orch, tmp_path, n=2):
    """Seed a session in SWAP_DONE with n swapped targets ready to animate."""
    src = _mk(tmp_path, "src.jpg")
    targets = [_mk(tmp_path, f"t{i}.jpg") for i in range(n)]
    handler.handle_source_intent(42)
    handler.consume_source(42, src)
    handler.handle_batch_intent(42)
    handler.consume_targets_album(42, targets)
    sess = orch.get(42)
    sess.status = STATE_SWAP_DONE
    for i, t in enumerate(sess.targets):
        t.swap_result_path = str(_mk(tmp_path, f"sw{i}.png"))
    return sess


@pytest.fixture
def recorded(monkeypatch):
    calls = []
    monkeypatch.setattr(_cost, "record_cost",
                        lambda uid, uname, amt: calls.append(amt))
    return calls


@pytest.fixture(autouse=True)
def _allow_limit(monkeypatch):
    monkeypatch.setattr(fsh, "check_limit", lambda uid, *, estimated_usd: (True, ""))


# ── _interpolate_batch unit behaviour (money-safe core) ─────────────────────────


@pytest.mark.asyncio
async def test_interpolate_all_success_bills_each(tmp_path, recorded, monkeypatch):
    monkeypatch.delenv("SWAPBATCH_RIFE_USD_PER_SEC", raising=False)
    h, _ = _handler(tmp_path)
    fake = _FakeRife()
    monkeypatch.setattr(h, "_make_rife_client", lambda: fake)
    vids = [_mk(tmp_path, "v0.mp4"), _mk(tmp_path, "v1.mp4")]

    out = await h._interpolate_batch(vids, 10, user_id=7, username="u")

    assert [p.name for p in out] == ["v0.smooth.mp4", "v1.smooth.mp4"]
    assert recorded == [pytest.approx(2 * 10 * 0.01)]   # billed for both


@pytest.mark.asyncio
async def test_interpolate_partial_failure_keeps_original(tmp_path, recorded, monkeypatch):
    monkeypatch.delenv("SWAPBATCH_RIFE_USD_PER_SEC", raising=False)
    h, _ = _handler(tmp_path)
    fake = _FakeRife(fail={1})           # second video fails
    monkeypatch.setattr(h, "_make_rife_client", lambda: fake)
    v0, v1 = _mk(tmp_path, "v0.mp4"), _mk(tmp_path, "v1.mp4")

    out = await h._interpolate_batch([v0, v1], 5, user_id=7, username="u")

    assert out[0].name == "v0.smooth.mp4"     # smoothed
    assert out[1] == v1                        # original delivered, not dropped
    assert recorded == [pytest.approx(1 * 5 * 0.01)]   # billed for the 1 success


@pytest.mark.asyncio
async def test_interpolate_all_fail_delivers_originals_bills_nothing(tmp_path, recorded, monkeypatch):
    h, _ = _handler(tmp_path)
    fake = _FakeRife(fail={0, 1})
    monkeypatch.setattr(h, "_make_rife_client", lambda: fake)
    v0, v1 = _mk(tmp_path, "v0.mp4"), _mk(tmp_path, "v1.mp4")

    out = await h._interpolate_batch([v0, v1], 10, user_id=7, username="u")

    assert out == [v0, v1]            # both originals, batch did not crash
    assert recorded == []            # zero RIFE charge


@pytest.mark.asyncio
async def test_client_construction_failure_delivers_originals(tmp_path, recorded, monkeypatch):
    # No WAVESPEED_API_KEY → _make_rife_client() raises. The batch must still
    # deliver every (un-smoothed) video and bill nothing for RIFE.
    h, _ = _handler(tmp_path)

    def _boom():
        raise WaveSpeedEngineError("WAVESPEED_API_KEY not set")
    monkeypatch.setattr(h, "_make_rife_client", _boom)
    v0, v1 = _mk(tmp_path, "v0.mp4"), _mk(tmp_path, "v1.mp4")

    out = await h._interpolate_batch([v0, v1], 10, user_id=7, username="u")

    assert out == [v0, v1]
    assert recorded == []


# ── pipeline wiring through run_animate_batch_phase ─────────────────────────────


async def _animate_fn(photos, cancel_check):
    # batch signature: aligns one video per swapped photo (all succeed)
    return [Path(p).with_name(f"anim{i}.mp4") for i, p in enumerate(photos)]


@pytest.mark.asyncio
async def test_smooth_off_skips_interpolation(tmp_path, monkeypatch):
    h, orch = _handler(tmp_path)
    _seed_animate_ready(h, orch, tmp_path, n=2)
    # smooth_enabled defaults to False — _interpolate_batch must NOT be called.
    called = {"n": 0}

    async def _spy(*a, **k):
        called["n"] += 1
        return list(a[0])
    monkeypatch.setattr(h, "_interpolate_batch", _spy)

    reply = await h.run_animate_batch_phase(42, _animate_fn, user_id=7, username="u")

    assert called["n"] == 0
    assert reply.videos and all(v.name.startswith("anim") for v in reply.videos)


@pytest.mark.asyncio
async def test_smooth_on_routes_through_interpolation(tmp_path, monkeypatch):
    monkeypatch.delenv("SWAPBATCH_RIFE_USD_PER_SEC", raising=False)
    h, orch = _handler(tmp_path)
    _seed_animate_ready(h, orch, tmp_path, n=2)
    orch.set_smooth(42, True)
    fake = _FakeRife()
    monkeypatch.setattr(h, "_make_rife_client", lambda: fake)

    reply = await h.run_animate_batch_phase(42, _animate_fn, user_id=7, username="u")

    # every delivered video is the smoothed variant
    assert reply.videos
    assert all(v.name.endswith(".smooth.mp4") for v in reply.videos)
    assert len(fake.calls) == 2


@pytest.mark.asyncio
async def test_interpolation_runs_after_animate_billing(tmp_path, recorded, monkeypatch):
    monkeypatch.delenv("SWAPBATCH_RIFE_USD_PER_SEC", raising=False)
    h, orch = _handler(tmp_path)
    sess = _seed_animate_ready(h, orch, tmp_path, n=2)
    seconds = sess.duration_sec
    orch.set_smooth(42, True)
    monkeypatch.setattr(h, "_make_rife_client", lambda: _FakeRife())

    await h.run_animate_batch_phase(42, _animate_fn, user_id=7, username="u")

    # exactly two charges, animate first then RIFE (order = money-safe contract)
    assert len(recorded) == 2
    from app.services.block_m2_video.engines.capabilities import caps_for
    animate_amt = 2 * caps_for(sess.video_engine).cost_for(seconds, sess.resolution)
    assert recorded[0] == pytest.approx(animate_amt)
    assert recorded[1] == pytest.approx(2 * max(1, seconds) * 0.01)
