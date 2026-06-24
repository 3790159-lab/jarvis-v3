# -*- coding: utf-8 -*-
"""Задача 4 (TDD): smooth UX button + RIFE surcharge in the cost estimate.

Two money-safe guarantees under test:
1. The estimate the user sees BEFORE /swapbatch_animate_go includes the RIFE
   surcharge when smooth is ON (full price up-front, no surprise charge).
2. The surcharge and the actual billing (_bill_interpolated_videos, Задача 2)
   are computed by the SAME shared formula/rate, so the quoted price can never
   diverge from the charged price.
"""
from pathlib import Path

import pytest
from PIL import Image

from app.handlers.face_swap_handler import (
    FaceSwapHandler,
    animate_cost_estimate,
    rife_surcharge_usd,
)
from app.services.audit import cost_tracker as _cost
from app.services.block_m2_face_swap.batch_orchestrator import BatchOrchestrator


class _V:
    def count_faces(self, p):
        return 1


def _jpg(p: Path) -> Path:
    Image.new("RGB", (16, 16), (1, 2, 3)).save(p, "JPEG")
    return p


def _orch(tmp_path):
    return BatchOrchestrator(state_root=tmp_path / "b", validator=_V())


async def _seed_swapped(orch, chat, tmp_path):
    orch.begin_source(chat)
    orch.submit_source(chat, _jpg(tmp_path / "s.jpg"))
    orch.begin_targets(chat)
    orch.add_targets(chat, [_jpg(tmp_path / "t0.jpg"), _jpg(tmp_path / "t1.jpg")])

    async def _swap(src, tgts, cc):
        return [_jpg(tmp_path / "r0.jpg"), _jpg(tmp_path / "r1.jpg")]

    await orch.confirm_swap(chat, swap_fn=_swap)


def _buttons(kb: dict) -> list[dict]:
    return [b for row in kb["inline_keyboard"] for b in row]


def _smooth_button(kb: dict) -> dict:
    for b in _buttons(kb):
        if b["callback_data"].startswith("sbsmooth:"):
            return b
    raise AssertionError("no smooth toggle button in keyboard")


def _has_smooth_button(kb: dict) -> bool:
    return any(b["callback_data"].startswith("sbsmooth:") for b in _buttons(kb))


# ── keyboard rendering ────────────────────────────────────────────────────────


def test_engine_keyboard_renders_smooth_off_state():
    kb = FaceSwapHandler.build_engine_keyboard(smooth_enabled=False)
    btn = _smooth_button(kb)
    assert "ВЫКЛ" in btn["text"]
    assert "Плавность" in btn["text"]
    assert btn["callback_data"] == "sbsmooth:on"   # tap turns it ON


def test_engine_keyboard_renders_smooth_on_state():
    kb = FaceSwapHandler.build_engine_keyboard(smooth_enabled=True)
    btn = _smooth_button(kb)
    assert "ВКЛ" in btn["text"]
    assert btn["callback_data"] == "sbsmooth:off"  # tap turns it OFF


def test_engine_keyboard_default_is_off():
    # No-arg call (existing callers) must keep working and show OFF.
    btn = _smooth_button(FaceSwapHandler.build_engine_keyboard())
    assert "ВЫКЛ" in btn["text"]


# ── show_smooth gate (standalone /animate hides the toggle) ─────────────────────


def test_swapbatch_menu_keeps_smooth_button():
    # The main swapbatch engine menu MUST keep the smooth toggle (default ON).
    kb = FaceSwapHandler.build_engine_keyboard()
    assert _has_smooth_button(kb)


def test_standalone_animate_menu_hides_smooth_button():
    # Standalone /animate has no batch session / smooth_enabled concept, so the
    # toggle would be dead/broken — it must be suppressed via show_smooth=False.
    kb = FaceSwapHandler.build_engine_keyboard(show_smooth=False)
    assert not _has_smooth_button(kb)
    # but the engine-choice rows must still be present
    cbs = [b["callback_data"] for b in _buttons(kb)]
    assert "sbeng:spicy" in cbs
    assert "sbeng:none" in cbs


# ── toggle callback handler ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_smooth_button_on_enables_and_redraws(tmp_path):
    orch = _orch(tmp_path)
    chat = 5
    await _seed_swapped(orch, chat, tmp_path)
    h = FaceSwapHandler(orchestrator=orch)
    kb = h.handle_smooth_button(chat, True)
    assert orch.get(chat).smooth_enabled is True
    btn = _smooth_button(kb)
    assert "ВКЛ" in btn["text"]
    assert btn["callback_data"] == "sbsmooth:off"


@pytest.mark.asyncio
async def test_smooth_button_off_disables_and_redraws(tmp_path):
    orch = _orch(tmp_path)
    chat = 6
    await _seed_swapped(orch, chat, tmp_path)
    h = FaceSwapHandler(orchestrator=orch)
    h.handle_smooth_button(chat, True)
    kb = h.handle_smooth_button(chat, False)
    assert orch.get(chat).smooth_enabled is False
    btn = _smooth_button(kb)
    assert "ВЫКЛ" in btn["text"]
    assert btn["callback_data"] == "sbsmooth:on"


# ── cost estimate surcharge ───────────────────────────────────────────────────


def test_estimate_smooth_on_adds_rife_surcharge(monkeypatch):
    monkeypatch.delenv("SWAPBATCH_RIFE_USD_PER_SEC", raising=False)
    est = animate_cost_estimate(
        swapped_count=2, seconds=10, resolution="720p",
        engine_mode="spicy", smooth_enabled=True,
    )
    # 2 videos × 10s × $0.01 = $0.20 surcharge on top of the animate cost.
    assert est["rife_surcharge_usd"] == pytest.approx(0.20)
    assert est["total_usd"] == pytest.approx(est["animate_usd"] + 0.20)


def test_estimate_smooth_off_no_surcharge_regression(monkeypatch):
    monkeypatch.delenv("SWAPBATCH_RIFE_USD_PER_SEC", raising=False)
    est = animate_cost_estimate(
        swapped_count=2, seconds=10, resolution="720p",
        engine_mode="spicy", smooth_enabled=False,
    )
    assert est["rife_surcharge_usd"] == pytest.approx(0.0)
    # total unchanged from the pre-smooth behaviour (== animate-only)
    assert est["total_usd"] == pytest.approx(est["animate_usd"])
    assert est["total_usd"] == pytest.approx(2.00)


def test_estimate_surcharge_equals_billing_amount(monkeypatch):
    # THE money-bug guard: quoted surcharge == amount actually billed, both via
    # the shared rife_surcharge_usd formula. If these diverge, the user is
    # charged something other than what they were shown.
    monkeypatch.delenv("SWAPBATCH_RIFE_USD_PER_SEC", raising=False)
    from app.services.block_m2_video.engines.capabilities import caps_for
    count, seconds, engine = 4, 5, "spicy"
    snapped = caps_for(engine).snap_duration(seconds)

    est = animate_cost_estimate(
        swapped_count=count, seconds=seconds, resolution="720p",
        engine_mode=engine, smooth_enabled=True,
    )

    recorded = []
    monkeypatch.setattr(_cost, "record_cost",
                        lambda uid, uname, amt: recorded.append(amt))
    FaceSwapHandler(orchestrator=None)._bill_interpolated_videos(
        count, snapped, user_id=1, username="u")
    charged = recorded[-1]

    assert charged == pytest.approx(rife_surcharge_usd(count, snapped))
    assert est["rife_surcharge_usd"] == pytest.approx(round(charged, 2))


def test_rife_surcharge_uses_env_rate(monkeypatch):
    monkeypatch.setenv("SWAPBATCH_RIFE_USD_PER_SEC", "0.02")
    est = animate_cost_estimate(
        swapped_count=2, seconds=10, resolution="720p",
        engine_mode="spicy", smooth_enabled=True,
    )
    assert est["rife_surcharge_usd"] == pytest.approx(0.40)   # 2×10×0.02


# ── user sees the full sum before /swapbatch_animate_go ────────────────────────


@pytest.mark.asyncio
async def test_animate_yes_total_includes_surcharge_when_smooth_on(tmp_path, monkeypatch):
    monkeypatch.delenv("SWAPBATCH_RIFE_USD_PER_SEC", raising=False)
    orch = _orch(tmp_path)
    chat = 9
    await _seed_swapped(orch, chat, tmp_path)
    orch.set_smooth(chat, True)
    h = FaceSwapHandler(orchestrator=orch)
    reply = h.handle_animate_yes(chat)
    # 2 swapped × 5s default × $0.01 = $0.10 RIFE on top; user must see it.
    assert "RIFE" in reply.text or "Плавность" in reply.text
    assert "$" in reply.text
