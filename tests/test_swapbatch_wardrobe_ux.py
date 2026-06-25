# -*- coding: utf-8 -*-
"""Задача 1 (TDD): wardrobe toggle button on the engine-choice keyboard.

One binary toggle mirroring the smooth button:
    preserve → ВКЛ (одет, anti-undress anchor + negative)
    spicy    → ВЫКЛ (uncensored, без ограничений)

`safe` never appears on the keyboard — it stays reachable only via the
/swapbatch_set_wardrobe command for fine 3-state control.

Critical (smooth lesson): redrawing one toggle must NOT reset the other's
label, so both handlers redraw the keyboard from the LIVE session state
(reading smooth_enabled AND wardrobe_mode).
"""
from pathlib import Path

import pytest
from PIL import Image

from app.handlers.face_swap_handler import FaceSwapHandler
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


def _wardrobe_button(kb: dict) -> dict:
    for b in _buttons(kb):
        if b["callback_data"].startswith("sbward:"):
            return b
    raise AssertionError("no wardrobe toggle button in keyboard")


def _has_wardrobe_button(kb: dict) -> bool:
    return any(b["callback_data"].startswith("sbward:") for b in _buttons(kb))


def _smooth_button(kb: dict) -> dict:
    for b in _buttons(kb):
        if b["callback_data"].startswith("sbsmooth:"):
            return b
    raise AssertionError("no smooth toggle button in keyboard")


# ── keyboard rendering ────────────────────────────────────────────────────────


def test_engine_keyboard_renders_wardrobe_on_state():
    kb = FaceSwapHandler.build_engine_keyboard(wardrobe_mode="preserve")
    btn = _wardrobe_button(kb)
    assert "ВКЛ" in btn["text"]
    assert "Не раздевать" in btn["text"]
    assert btn["callback_data"] == "sbward:off"   # tap turns it OFF (→ spicy)


def test_engine_keyboard_renders_wardrobe_off_state():
    kb = FaceSwapHandler.build_engine_keyboard(wardrobe_mode="spicy")
    btn = _wardrobe_button(kb)
    assert "ВЫКЛ" in btn["text"]
    assert btn["callback_data"] == "sbward:on"    # tap turns it ON (→ preserve)


def test_engine_keyboard_default_is_on():
    # No-arg call must show the safe default (preserve → ВКЛ).
    btn = _wardrobe_button(FaceSwapHandler.build_engine_keyboard())
    assert "ВКЛ" in btn["text"]
    assert btn["callback_data"] == "sbward:off"


# ── show_wardrobe gate (standalone /animate hides the toggle) ───────────────────


def test_swapbatch_menu_keeps_wardrobe_button():
    kb = FaceSwapHandler.build_engine_keyboard()
    assert _has_wardrobe_button(kb)


def test_standalone_animate_menu_hides_wardrobe_button():
    # Standalone /animate has no batch session → set_wardrobe is a no-op and the
    # toggle would be dead; it must be suppressed via show_wardrobe=False.
    kb = FaceSwapHandler.build_engine_keyboard(show_wardrobe=False)
    assert not _has_wardrobe_button(kb)
    cbs = [b["callback_data"] for b in _buttons(kb)]
    assert "sbeng:spicy" in cbs
    assert "sbeng:none" in cbs


# ── toggle callback handler ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wardrobe_button_on_sets_preserve_and_redraws(tmp_path):
    orch = _orch(tmp_path)
    chat = 5
    await _seed_swapped(orch, chat, tmp_path)
    h = FaceSwapHandler(orchestrator=orch)
    kb = h.handle_wardrobe_button(chat, True)
    assert orch.get(chat).wardrobe_mode == "preserve"
    btn = _wardrobe_button(kb)
    assert "ВКЛ" in btn["text"]
    assert btn["callback_data"] == "sbward:off"


@pytest.mark.asyncio
async def test_wardrobe_button_off_sets_spicy_and_redraws(tmp_path):
    orch = _orch(tmp_path)
    chat = 6
    await _seed_swapped(orch, chat, tmp_path)
    h = FaceSwapHandler(orchestrator=orch)
    kb = h.handle_wardrobe_button(chat, False)
    assert orch.get(chat).wardrobe_mode == "spicy"
    btn = _wardrobe_button(kb)
    assert "ВЫКЛ" in btn["text"]
    assert btn["callback_data"] == "sbward:on"


@pytest.mark.asyncio
async def test_seeded_session_defaults_to_wardrobe_on(tmp_path):
    # A fresh batch session now defaults to preserve → the toggle shows ВКЛ.
    orch = _orch(tmp_path)
    chat = 7
    await _seed_swapped(orch, chat, tmp_path)
    assert orch.get(chat).wardrobe_mode == "preserve"
    kb = FaceSwapHandler.build_engine_keyboard(
        wardrobe_mode=orch.get(chat).wardrobe_mode)
    assert "ВКЛ" in _wardrobe_button(kb)["text"]


# ── REGRESSION: redrawing one toggle must not reset the other's label ──────────


@pytest.mark.asyncio
async def test_smooth_toggle_preserves_wardrobe_label(tmp_path):
    orch = _orch(tmp_path)
    chat = 8
    await _seed_swapped(orch, chat, tmp_path)
    h = FaceSwapHandler(orchestrator=orch)
    # User turned wardrobe OFF (spicy); now toggles smooth ON.
    h.handle_wardrobe_button(chat, False)
    kb = h.handle_smooth_button(chat, True)
    # smooth label updated…
    assert "ВКЛ" in _smooth_button(kb)["text"]
    # …and wardrobe label is NOT silently reset to the default ВКЛ.
    assert "ВЫКЛ" in _wardrobe_button(kb)["text"]
    assert orch.get(chat).wardrobe_mode == "spicy"


@pytest.mark.asyncio
async def test_wardrobe_toggle_preserves_smooth_label(tmp_path):
    orch = _orch(tmp_path)
    chat = 9
    await _seed_swapped(orch, chat, tmp_path)
    h = FaceSwapHandler(orchestrator=orch)
    # User turned smooth ON; now toggles wardrobe OFF.
    h.handle_smooth_button(chat, True)
    kb = h.handle_wardrobe_button(chat, False)
    # wardrobe label updated…
    assert "ВЫКЛ" in _wardrobe_button(kb)["text"]
    # …and smooth label is NOT silently reset to the default ВЫКЛ.
    assert "ВКЛ" in _smooth_button(kb)["text"]
    assert orch.get(chat).smooth_enabled is True
