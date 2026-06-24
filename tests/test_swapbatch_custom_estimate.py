# -*- coding: utf-8 -*-
"""Задача 3 (TDD): cost estimate for the per-photo custom flow, shown BEFORE
the paid confirm step.

Money-safe contract: the custom estimate the user sees must equal what the
custom run will actually bill — same caps.cost_for(...) and same
rife_surcharge_usd as the default path (one shared mechanism:
animate_cost_estimate). No flat 0.27, no quote-vs-charge drift.
"""
from pathlib import Path

import pytest
from PIL import Image

from app.handlers.face_swap_handler import (
    FaceSwapHandler,
    animate_cost_estimate,
)
from app.services.block_m2_face_swap.batch_orchestrator import (
    BatchOrchestrator,
    STATE_SWAP_DONE,
)


class _V:
    def count_faces(self, p):
        return 1


def _jpg(p: Path) -> Path:
    Image.new("RGB", (16, 16), (1, 2, 3)).save(p, "JPEG")
    return p


def _seed_confirm(tmp_path, *, n=3, engine="spicy", duration=5,
                  resolution="720p", smooth=False):
    """Drive chat 42 to AWAITING_CUSTOM_PROMPTS_CONFIRM with the preview built."""
    orch = BatchOrchestrator(state_root=tmp_path / "b", validator=_V())
    h = FaceSwapHandler(orchestrator=orch)
    orch.begin_source(42)
    orch.submit_source(42, _jpg(tmp_path / "s.jpg"))
    orch.begin_targets(42)
    orch.add_targets(42, [_jpg(tmp_path / f"t{i}.jpg") for i in range(n)])
    sess = orch.get(42)
    sess.status = STATE_SWAP_DONE
    for i, t in enumerate(sess.targets):
        t.swap_result_path = str(_jpg(tmp_path / f"sw{i}.png"))
    sess.duration_sec = duration
    sess.resolution = resolution
    sess.video_engine = engine
    sess.smooth_enabled = smooth
    h.handle_animate_custom(42)
    # one prompt per photo
    text = "\n".join(f"{i}. move {i}" for i in range(1, n + 1))
    reply = h.consume_custom_prompts_text(42, text)
    return h, orch, sess, reply


def test_custom_preview_shows_caps_estimate(tmp_path, monkeypatch):
    monkeypatch.delenv("SWAPBATCH_RIFE_USD_PER_SEC", raising=False)
    _, _, _, reply = _seed_confirm(tmp_path, n=3, engine="spicy",
                                   duration=5, resolution="720p", smooth=False)
    est = animate_cost_estimate(
        swapped_count=3, seconds=5, resolution="720p",
        engine_mode="spicy", smooth_enabled=False,
    )
    # the preview the user sees BEFORE /swapbatch_confirm must quote the cost
    assert "Стоимость" in reply.text
    assert f"${est['total_usd']:.2f}" in reply.text


def test_custom_estimate_equals_default_path_estimate(tmp_path, monkeypatch):
    # Same engine/duration/count → identical estimate to /swapbatch_animate_yes
    # (both go through animate_cost_estimate → caps.cost_for, NOT flat 0.27).
    monkeypatch.delenv("SWAPBATCH_RIFE_USD_PER_SEC", raising=False)
    h, _, _, reply = _seed_confirm(tmp_path, n=3, engine="spicy",
                                   duration=5, resolution="720p", smooth=False)
    est = animate_cost_estimate(
        swapped_count=3, seconds=5, resolution="720p",
        engine_mode="spicy", smooth_enabled=False,
    )
    from app.services.block_m2_video.engines.capabilities import caps_for
    expected = round(3 * caps_for("spicy").cost_for(5, "720p"), 2)
    assert est["total_usd"] == pytest.approx(expected)   # caps, not 0.27 flat
    assert f"${expected:.2f}" in reply.text


def test_custom_estimate_includes_rife_when_smooth_on(tmp_path, monkeypatch):
    monkeypatch.delenv("SWAPBATCH_RIFE_USD_PER_SEC", raising=False)
    _, _, _, reply = _seed_confirm(tmp_path, n=2, engine="spicy",
                                   duration=10, resolution="720p", smooth=True)
    est = animate_cost_estimate(
        swapped_count=2, seconds=10, resolution="720p",
        engine_mode="spicy", smooth_enabled=True,
    )
    assert est["rife_surcharge_usd"] > 0
    assert "RIFE" in reply.text or "Плавность" in reply.text
    assert f"${est['total_usd']:.2f}" in reply.text


def test_custom_estimate_no_rife_line_when_smooth_off(tmp_path, monkeypatch):
    monkeypatch.delenv("SWAPBATCH_RIFE_USD_PER_SEC", raising=False)
    _, _, _, reply = _seed_confirm(tmp_path, n=2, engine="spicy",
                                   duration=10, resolution="720p", smooth=False)
    assert "Плавность 48fps (RIFE)" not in reply.text


def test_custom_estimate_counts_each_photo_as_one_video(tmp_path, monkeypatch):
    # Estimate scales with the number of swapped photos in the batch.
    monkeypatch.delenv("SWAPBATCH_RIFE_USD_PER_SEC", raising=False)
    _, _, _, reply = _seed_confirm(tmp_path, n=4, engine="spicy",
                                   duration=5, resolution="720p", smooth=False)
    est = animate_cost_estimate(
        swapped_count=4, seconds=5, resolution="720p",
        engine_mode="spicy", smooth_enabled=False,
    )
    assert est["count"] == 4
    assert f"${est['total_usd']:.2f}" in reply.text
