# -*- coding: utf-8 -*-
"""Задача 2 (Вариант A) — КУЛЬМИНАЦИЯ: money-safe + reuse integration.

Task 7 (the heart of the feature): a spy on the cost ledger proves that the
full ready-photo path bills ONLY animation — the swap is never billed because
``run_swap_phase`` (its only billing site) is never on the path. And
``check_limit`` gates spending so a friend over budget is blocked before a cent
is spent.

Task 8: every second-stage feature (per-photo prompts, smooth, wardrobe) works
on ready photos exactly as on swapped ones.
"""
from pathlib import Path

import pytest
from PIL import Image

from app.handlers.face_swap_handler import FaceSwapHandler
from app.services.block_m2_face_swap.batch_orchestrator import (
    BatchOrchestrator,
    STATE_AWAITING_CUSTOM_PROMPTS,
    STATE_SWAP_DONE,
)
from app.services.block_m2_video.engines.capabilities import caps_for


class _V:
    def count_faces(self, p):
        Image.open(p).verify()
        return 1


def _jpg(p: Path) -> Path:
    Image.new("RGB", (16, 16), (1, 2, 3)).save(p, "JPEG")
    return p


def _orch(tmp_path):
    return BatchOrchestrator(state_root=tmp_path / "b", validator=_V())


def _seed_ready(orch, chat, tmp_path, n=2):
    """begin -> add_ready -> finish: a ready batch parked in SWAP_DONE."""
    h = FaceSwapHandler(orchestrator=orch)
    h.handle_animate_batch_intent(chat)
    paths = [_jpg(tmp_path / f"r{i}.jpg") for i in range(n)]
    h.consume_ready_album(chat, paths)
    h.handle_animate_batch_go(chat)
    return h


# ── Task 7: money-safe spy ledger (THE central test) ─────────────────────────

@pytest.mark.asyncio
async def test_ready_batch_bills_animate_only_never_swap(tmp_path, monkeypatch):
    orch = _orch(tmp_path)
    chat = 40
    h = _seed_ready(orch, chat, tmp_path, n=2)
    assert orch.status(chat) == STATE_SWAP_DONE
    sess = orch.get(chat)  # hold a ref so prune() doesn't lose our assertions

    # Spy the ledger: BOTH swap and animate would record here. If a swap were
    # billed it would show as a second call — so call count is the proof.
    recorded: list[float] = []
    monkeypatch.setattr(
        "app.handlers.face_swap_handler._cost.record_cost",
        lambda uid, uname, amount: recorded.append(amount),
    )
    # Spy check_limit: capture the estimate, allow the spend.
    limit_calls: list[float] = []

    def _fake_check_limit(user_id, *, estimated_usd):
        limit_calls.append(estimated_usd)
        return True, ""

    monkeypatch.setattr(
        "app.handlers.face_swap_handler.check_limit", _fake_check_limit
    )

    async def _animate_fn(photos, cancel_check):
        return [tmp_path / f"v{i}.mp4" for i in range(len(photos))]

    reply = await h.run_animate_batch_phase(
        chat, _animate_fn, user_id=chat, username="daniil",
    )

    per_video = caps_for("spicy").cost_for(sess.duration_sec, sess.resolution)
    expected_animate = 2 * per_video

    # check_limit was called BEFORE spending, with the animate estimate.
    assert limit_calls == [pytest.approx(expected_animate)]
    # EXACTLY ONE ledger write — the animate one. A swap bill would be a 2nd.
    assert len(recorded) == 1
    assert recorded[0] == pytest.approx(expected_animate)
    # Videos were really produced.
    assert sum(1 for t in sess.targets if t.animate_result_path) == 2
    assert reply.videos and len(reply.videos) == 2


@pytest.mark.asyncio
async def test_friend_over_limit_blocked_before_any_spend(tmp_path, monkeypatch):
    orch = _orch(tmp_path)
    chat = 41
    h = _seed_ready(orch, chat, tmp_path, n=2)

    recorded: list[float] = []
    monkeypatch.setattr(
        "app.handlers.face_swap_handler._cost.record_cost",
        lambda uid, uname, amount: recorded.append(amount),
    )
    monkeypatch.setattr(
        "app.handlers.face_swap_handler.check_limit",
        lambda user_id, *, estimated_usd: (False, "лимит исчерпан"),
    )

    async def _animate_fn(photos, cancel_check):
        raise AssertionError("animate_fn must not run when over limit")

    reply = await h.run_animate_batch_phase(
        chat, _animate_fn, user_id=chat, username="friend",
    )
    assert "🚫" in reply.text
    assert recorded == []  # not a single cent spent


# ── Task 8: reuse — every second-stage feature works on ready photos ─────────

def test_custom_prompts_flow_works_on_ready_photos(tmp_path):
    orch = _orch(tmp_path)
    chat = 42
    h = _seed_ready(orch, chat, tmp_path, n=2)
    reply = h.handle_animate_custom(chat)
    assert orch.status(chat) == STATE_AWAITING_CUSTOM_PROMPTS
    assert reply.numbered_photos and len(reply.numbered_photos) == 2


def test_smooth_toggle_mutates_ready_session(tmp_path):
    orch = _orch(tmp_path)
    chat = 43
    _seed_ready(orch, chat, tmp_path, n=1)
    assert orch.get(chat).smooth_enabled is False  # money-safe default
    orch.set_smooth(chat, True)
    assert orch.get(chat).smooth_enabled is True


def test_wardrobe_toggle_mutates_ready_session(tmp_path):
    orch = _orch(tmp_path)
    chat = 44
    _seed_ready(orch, chat, tmp_path, n=1)
    assert orch.get(chat).wardrobe_mode == "preserve"  # money-safe default
    orch.set_wardrobe(chat, "spicy")
    assert orch.get(chat).wardrobe_mode == "spicy"
    orch.set_wardrobe(chat, "preserve")
    assert orch.get(chat).wardrobe_mode == "preserve"
