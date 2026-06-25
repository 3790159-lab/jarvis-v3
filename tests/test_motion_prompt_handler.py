"""Tests for Task 2: handler-glue handle_generate_prompt.

handle_generate_prompt(chat_id):
  - picks the FIRST batch target that has a swap_result_path
  - calls motion_prompt_ai.generate_motion_prompt on it
  - success -> orchestrator.set_motion_prompt + reply with prompt + 3 options
  - no active batch / no swapped photo -> soft error, generator NOT called
  - generator None -> soft error, set_motion_prompt NOT called (prompt kept)

Money/check_limit (Task 3) and the button/callback/thread (Task 4) are out of
scope here — this is the pure handler.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from app.handlers.face_swap_handler import FaceSwapHandler
from app.services.block_m2_face_swap.batch_orchestrator import BatchOrchestrator
from app.services.block_m2_video import motion_prompt_ai

CHAT = 42


def _make_photo(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
    return p


def _make_handler(tmp_path: Path):
    v = MagicMock()
    v.count_faces.return_value = 1
    orch = BatchOrchestrator(state_root=tmp_path / "batches", validator=v)
    return FaceSwapHandler(orchestrator=orch), orch


def _seed_targets(handler, orch, tmp_path, n=2):
    """Drive the batch up to targets-received (no swap_result_path yet)."""
    src = _make_photo(tmp_path, "src.jpg")
    targets = [_make_photo(tmp_path, f"t{i}.jpg") for i in range(n)]
    handler.handle_source_intent(CHAT)
    handler.consume_source(CHAT, src)
    handler.handle_batch_intent(CHAT)
    handler.consume_targets_album(CHAT, targets)
    return orch.get(CHAT)


def test_success_sets_motion_prompt_and_replies_with_options(tmp_path, monkeypatch):
    handler, orch = _make_handler(tmp_path)
    sess = _seed_targets(handler, orch, tmp_path, n=2)
    sess.targets[0].swap_result_path = str(_make_photo(tmp_path, "sw0.png"))

    generated = "slow gentle head turn, soft blinking, locked static camera, photorealistic"
    monkeypatch.setattr(motion_prompt_ai, "generate_motion_prompt", lambda p: generated)

    r = handler.handle_generate_prompt(CHAT)

    # motion_prompt persisted on the session
    assert orch.get(CHAT).motion_prompt == generated
    # reply shows the generated prompt + the 3 options
    assert generated in r.text
    assert "/swapbatch_set_prompt" in r.text  # rewrite option
    assert "ещё раз" in r.text  # regenerate option
    assert "движок" in r.text.lower()  # accept -> choose engine option


def test_uses_first_target_with_swap_result_path(tmp_path, monkeypatch):
    handler, orch = _make_handler(tmp_path)
    sess = _seed_targets(handler, orch, tmp_path, n=3)
    # target[0] has NO swap result; [1] and [2] do -> must pick [1]
    first_swapped = str(_make_photo(tmp_path, "sw1.png"))
    sess.targets[1].swap_result_path = first_swapped
    sess.targets[2].swap_result_path = str(_make_photo(tmp_path, "sw2.png"))

    captured = {}

    def fake_gen(path):
        captured["path"] = path
        return "slow gentle head turn, locked static camera, photorealistic"

    monkeypatch.setattr(motion_prompt_ai, "generate_motion_prompt", fake_gen)

    handler.handle_generate_prompt(CHAT)

    assert captured["path"] == first_swapped


def test_no_active_batch_soft_error_generator_not_called(tmp_path, monkeypatch):
    handler, orch = _make_handler(tmp_path)

    def boom(path):
        raise AssertionError("generator must not be called without a batch")

    monkeypatch.setattr(motion_prompt_ai, "generate_motion_prompt", boom)

    r = handler.handle_generate_prompt(CHAT)

    assert "⚠️" in r.text


def test_no_swapped_photo_soft_error_generator_not_called(tmp_path, monkeypatch):
    handler, orch = _make_handler(tmp_path)
    _seed_targets(handler, orch, tmp_path, n=2)  # targets exist but no swap_result_path

    def boom(path):
        raise AssertionError("generator must not be called without a swapped photo")

    monkeypatch.setattr(motion_prompt_ai, "generate_motion_prompt", boom)

    r = handler.handle_generate_prompt(CHAT)

    assert "⚠️" in r.text


def test_generator_none_soft_error_keeps_existing_prompt(tmp_path, monkeypatch):
    handler, orch = _make_handler(tmp_path)
    sess = _seed_targets(handler, orch, tmp_path, n=1)
    sess.targets[0].swap_result_path = str(_make_photo(tmp_path, "sw0.png"))
    orch.set_motion_prompt(CHAT, "previous good prompt")

    monkeypatch.setattr(motion_prompt_ai, "generate_motion_prompt", lambda p: None)
    spy = MagicMock(wraps=orch.set_motion_prompt)
    monkeypatch.setattr(orch, "set_motion_prompt", spy)

    r = handler.handle_generate_prompt(CHAT)

    assert "⚠️" in r.text
    spy.assert_not_called()  # must not overwrite with junk
    assert orch.get(CHAT).motion_prompt == "previous good prompt"
