from __future__ import annotations

import importlib
import sys

import pytest

from app.services.block_m2_video import prompt_intake


@pytest.fixture(autouse=True)
def _clean_state():
    """Each test starts with an empty awaiting-table (module-level state)."""
    prompt_intake._AWAITING.clear()
    yield
    prompt_intake._AWAITING.clear()


def test_not_awaiting_before_arm():
    assert prompt_intake.is_awaiting(111) is False
    assert prompt_intake.awaiting_kind(111) is None


def test_arm_sets_awaiting():
    prompt_intake.arm(111, "animate")
    assert prompt_intake.is_awaiting(111) is True
    assert prompt_intake.awaiting_kind(111) == "animate"


def test_consume_when_not_awaiting_returns_none():
    # Router must be able to call consume unconditionally and fall through.
    assert prompt_intake.consume(111, "some text") is None


def test_consume_returns_result_and_clears_awaiting():
    prompt_intake.arm(111, "animate")
    result = prompt_intake.consume(111, "she turns slowly")
    assert result is not None
    assert result.kind == "animate"
    assert result.motion == "she turns slowly"
    assert result.truncated is False
    # Awaiting is cleared after a consume (single-shot).
    assert prompt_intake.is_awaiting(111) is False


def test_consume_clamps_to_cap(monkeypatch):
    monkeypatch.setenv("WAVESPEED_PROMPT_MAX_CHARS", "50")
    prompt_intake.arm(111, "videoref")
    long_text = "word " * 40  # 200 chars, far over the 50 cap
    result = prompt_intake.consume(111, long_text)
    assert result.truncated is True
    assert len(result.motion) <= 50


def test_consume_at_cap_boundary_not_truncated(monkeypatch):
    monkeypatch.setenv("WAVESPEED_PROMPT_MAX_CHARS", "10")
    prompt_intake.arm(111, "animate")
    result = prompt_intake.consume(111, "1234567890")  # exactly 10
    assert result.truncated is False
    assert result.motion == "1234567890"


def test_whitespace_only_yields_empty_motion():
    prompt_intake.arm(111, "animate")
    result = prompt_intake.consume(111, "   \n  ")
    assert result is not None
    assert result.motion == ""


def test_disarm_clears_awaiting():
    prompt_intake.arm(111, "videoref")
    prompt_intake.disarm(111)
    assert prompt_intake.is_awaiting(111) is False


def test_chats_isolated():
    prompt_intake.arm(111, "animate")
    assert prompt_intake.is_awaiting(222) is False


def test_seam_imports_nothing_paid():
    """Money-tooth: the seam must not pull in Grok/engine modules (it is free)."""
    # Reimport in isolation and assert the paid module was not dragged in by it.
    for mod in list(sys.modules):
        if mod.startswith("app.services.block_m2_video.motion_prompt_ai"):
            del sys.modules[mod]
    importlib.reload(prompt_intake)
    assert "app.services.block_m2_video.motion_prompt_ai" not in sys.modules
