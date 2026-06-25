"""Tests for Task 3: money + friend gate on handle_generate_prompt.

Mirrors the paid-path convention of run_animate_batch_phase:
  - check_limit(user_id, estimated_usd≈0.01) STRICTLY before the vision call
  - vision runs ONLY if the limit check passed
  - record_cost($0.01) only AFTER a successful generation (never on None/error)
  - admin is unlimited; friend over-limit is refused with 🚫 and zero spend

Spy ledger has TEETH: over-limit must produce zero vision calls and zero
ledger writes — the call counts are the proof.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.handlers.face_swap_handler import FaceSwapHandler
from app.services.block_m2_face_swap.batch_orchestrator import BatchOrchestrator
from app.services.block_m2_video import motion_prompt_ai

CHAT = 42
FRIEND = 555
ADMIN = 237616472
PROMPT = "slow gentle head turn, soft blinking, locked static camera, photorealistic"


def _make_photo(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
    return p


def _make_handler(tmp_path: Path):
    v = MagicMock()
    v.count_faces.return_value = 1
    orch = BatchOrchestrator(state_root=tmp_path / "batches", validator=v)
    return FaceSwapHandler(orchestrator=orch), orch


def _seed_swapped(handler, orch, tmp_path):
    """Batch with one swapped photo ready for prompt generation."""
    src = _make_photo(tmp_path, "src.jpg")
    targets = [_make_photo(tmp_path, "t0.jpg")]
    handler.handle_source_intent(CHAT)
    handler.consume_source(CHAT, src)
    handler.handle_batch_intent(CHAT)
    handler.consume_targets_album(CHAT, targets)
    sess = orch.get(CHAT)
    sess.targets[0].swap_result_path = str(_make_photo(tmp_path, "sw0.png"))
    return sess


def _spy_ledger(monkeypatch):
    """Returns (recorded_amounts, limit_estimates). Spies with teeth."""
    recorded: list[float] = []
    monkeypatch.setattr(
        "app.handlers.face_swap_handler._cost.record_cost",
        lambda uid, uname, amount: recorded.append(amount),
    )
    return recorded


def test_under_limit_runs_vision_and_bills_one_cent(tmp_path, monkeypatch):
    handler, orch = _make_handler(tmp_path)
    _seed_swapped(handler, orch, tmp_path)
    recorded = _spy_ledger(monkeypatch)

    limit_estimates: list[float] = []

    def fake_check(user_id, *, estimated_usd):
        limit_estimates.append(estimated_usd)
        return True, ""

    monkeypatch.setattr("app.handlers.face_swap_handler.check_limit", fake_check)

    vision_calls: list[str] = []

    def fake_gen(path):
        vision_calls.append(path)
        return PROMPT

    monkeypatch.setattr(motion_prompt_ai, "generate_motion_prompt", fake_gen)

    r = handler.handle_generate_prompt(CHAT, user_id=FRIEND, username="petya")

    assert len(vision_calls) == 1  # vision ran
    assert limit_estimates == [pytest.approx(0.01)]  # checked ~1 cent BEFORE
    assert recorded == [pytest.approx(0.01)]  # billed exactly one cent AFTER
    assert orch.get(CHAT).motion_prompt == PROMPT
    assert PROMPT in r.text


def test_friend_over_limit_refused_no_vision_no_charge(tmp_path, monkeypatch):
    handler, orch = _make_handler(tmp_path)
    _seed_swapped(handler, orch, tmp_path)
    recorded = _spy_ledger(monkeypatch)

    monkeypatch.setattr(
        "app.handlers.face_swap_handler.check_limit",
        lambda user_id, *, estimated_usd: (False, "Дневной лимит исчерпан"),
    )

    def boom(path):
        raise AssertionError("vision must NOT run when over the limit")

    monkeypatch.setattr(motion_prompt_ai, "generate_motion_prompt", boom)

    r = handler.handle_generate_prompt(CHAT, user_id=FRIEND, username="petya")

    assert "🚫" in r.text  # refusal
    assert recorded == []  # not a single cent spent
    # vision NOT called proven by boom never firing


def test_admin_is_unlimited_and_runs_vision(tmp_path, monkeypatch):
    # Real check_limit with admin env -> unlimited (no mock of the gate).
    monkeypatch.setenv("JARVIS_USERS_FILE", str(tmp_path / "users.json"))
    monkeypatch.setenv("JARVIS_COST_FILE", str(tmp_path / "cost.json"))
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", str(ADMIN))

    handler, orch = _make_handler(tmp_path)
    _seed_swapped(handler, orch, tmp_path)
    _spy_ledger(monkeypatch)

    vision_calls: list[str] = []

    def fake_gen(path):
        vision_calls.append(path)
        return PROMPT

    monkeypatch.setattr(motion_prompt_ai, "generate_motion_prompt", fake_gen)

    r = handler.handle_generate_prompt(CHAT, user_id=ADMIN, username="daniil")

    assert len(vision_calls) == 1  # admin sailed through the gate
    assert "🚫" not in r.text
    assert PROMPT in r.text


def test_generator_none_after_passing_check_does_not_bill(tmp_path, monkeypatch):
    handler, orch = _make_handler(tmp_path)
    _seed_swapped(handler, orch, tmp_path)
    recorded = _spy_ledger(monkeypatch)

    limit_calls: list[float] = []

    def fake_check(user_id, *, estimated_usd):
        limit_calls.append(estimated_usd)
        return True, ""

    monkeypatch.setattr("app.handlers.face_swap_handler.check_limit", fake_check)
    monkeypatch.setattr(motion_prompt_ai, "generate_motion_prompt", lambda p: None)

    r = handler.handle_generate_prompt(CHAT, user_id=FRIEND, username="petya")

    assert "⚠️" in r.text  # soft error
    assert len(limit_calls) == 1  # we DID pay the limit-check...
    assert recorded == []  # ...but billed nothing for the failed generation


def test_check_limit_strictly_before_vision(tmp_path, monkeypatch):
    handler, orch = _make_handler(tmp_path)
    _seed_swapped(handler, orch, tmp_path)
    _spy_ledger(monkeypatch)

    events: list[str] = []

    def fake_check(user_id, *, estimated_usd):
        events.append("check")
        return True, ""

    def fake_gen(path):
        events.append("gen")
        return PROMPT

    monkeypatch.setattr("app.handlers.face_swap_handler.check_limit", fake_check)
    monkeypatch.setattr(motion_prompt_ai, "generate_motion_prompt", fake_gen)

    handler.handle_generate_prompt(CHAT, user_id=FRIEND, username="petya")

    assert events == ["check", "gen"]  # gate fires BEFORE the paid vision call
