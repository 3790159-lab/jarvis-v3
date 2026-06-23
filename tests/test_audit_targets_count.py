# -*- coding: utf-8 -*-
"""Bug: admin tracker shows "Swap started: 0 targets" / "0 videos".

Root cause: ``_audit_message`` emits ``swapbatch_go`` / ``animate_started``
from the bare command text and never populates ``details["targets"]`` — so the
admin-forward formatter always falls back to its ``0`` default, for EVERY user.
(The admin never noticed on himself because ``audit_logger._maybe_forward``
suppresses forwarding the admin's own activity to the admin — line :192.)

Fix (variant 1): ``_audit_message`` looks up the live orchestrator session and
writes the real count into ``details["targets"]``:
  - swapbatch_go            → len(session.targets)
  - animate_started yes/custom → number of successfully-swapped photos
  - animate_started no       → 0 (user chose NOT to animate)

These tests exercise a NON-admin chat_id (the reported symptom), guard the
self-suppression at audit_logger:192, and the no-session safety path.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.audit import audit_logger  # noqa: E402

# A non-admin friend (the reported victim: @Artem_koval3).
FRIEND_ID = 545893540
ADMIN_ID = 237616472


def _get_mod():
    mod_name = f"_test_targets_{id(object())}"
    spec = importlib.util.spec_from_file_location(
        mod_name, ROOT / "tools" / "jarvis_smart_telegram_control.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _text_update(user_id: int, chat_id: int, text: str, username: str = "tester") -> dict:
    return {
        "update_id": 1,
        "message": {
            "from": {"id": user_id, "username": username},
            "chat": {"id": chat_id},
            "text": text,
        },
    }


def _orch_with(targets):
    """Fake orchestrator whose .get(cid) returns a session holding ``targets``."""
    sess = SimpleNamespace(targets=list(targets))
    return SimpleNamespace(get=lambda cid: sess)


def _orch_empty():
    """Fake orchestrator with no active session (returns None)."""
    return SimpleNamespace(get=lambda cid: None)


def _run(mod, upd, orch):
    captured: list[tuple[str, dict]] = []

    def _spy(user_id, username, chat_id, event, details=None, **kw):
        captured.append((event, details or {}))

    with patch.object(mod._audit, "audit_event", _spy), \
         patch.object(mod, "send", lambda cid, txt, **k: None), \
         patch.object(mod, "handle", lambda cid, txt: None), \
         patch.object(mod, "_swapbatch_text_intercept", lambda *a, **k: False), \
         patch.object(mod, "_swapbatch_get_handler", lambda: (None, orch)):
        mod.process_update(upd)
    return captured


# ── swapbatch_go: count = number of accepted targets ─────────────────────────


def test_swapbatch_go_targets_count_for_non_admin(monkeypatch):
    """Non-admin /swapbatch_go records details['targets'] = len(session.targets)."""
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", str(ADMIN_ID))
    monkeypatch.setenv("JARVIS_ALLOWED_USER_IDS", str(FRIEND_ID))
    mod = _get_mod()

    targets = [SimpleNamespace(swap_result_path=None) for _ in range(3)]
    upd = _text_update(FRIEND_ID, FRIEND_ID, "/swapbatch_go", "Artem_koval3")
    captured = _run(mod, upd, _orch_with(targets))

    go = [d for e, d in captured if e == "swapbatch_go"]
    assert go, f"expected swapbatch_go event, got {captured}"
    assert go[0].get("targets") == 3


# ── animate_started: yes/custom = swapped count, no = 0 ───────────────────────


def test_animate_started_yes_counts_swapped_photos_for_non_admin(monkeypatch):
    """Non-admin /swapbatch_animate_yes → targets = # of swapped photos."""
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", str(ADMIN_ID))
    monkeypatch.setenv("JARVIS_ALLOWED_USER_IDS", str(FRIEND_ID))
    mod = _get_mod()

    # 2 swapped (have a result path) + 1 failed (None) → expect 2.
    targets = [
        SimpleNamespace(swap_result_path="/out/a.png"),
        SimpleNamespace(swap_result_path="/out/b.png"),
        SimpleNamespace(swap_result_path=None),
    ]
    upd = _text_update(FRIEND_ID, FRIEND_ID, "/swapbatch_animate_yes", "Artem_koval3")
    captured = _run(mod, upd, _orch_with(targets))

    ev = [d for e, d in captured if e == "animate_started"]
    assert ev, f"expected animate_started, got {captured}"
    assert ev[0].get("mode") == "yes"
    assert ev[0].get("targets") == 2


def test_animate_started_no_reports_zero_videos(monkeypatch):
    """/swapbatch_no maps to animate_started(no) — 0 videos are produced."""
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", str(ADMIN_ID))
    monkeypatch.setenv("JARVIS_ALLOWED_USER_IDS", str(FRIEND_ID))
    mod = _get_mod()

    targets = [SimpleNamespace(swap_result_path="/out/a.png") for _ in range(4)]
    upd = _text_update(FRIEND_ID, FRIEND_ID, "/swapbatch_no", "Artem_koval3")
    captured = _run(mod, upd, _orch_with(targets))

    ev = [d for e, d in captured if e == "animate_started"]
    assert ev, f"expected animate_started, got {captured}"
    assert ev[0].get("mode") == "no"
    assert ev[0].get("targets") == 0


# ── no active session → does not crash, count defaults to 0 ───────────────────


def test_swapbatch_go_no_session_is_safe(monkeypatch):
    """No active session → event still emitted, count effectively 0, no crash."""
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", str(ADMIN_ID))
    monkeypatch.setenv("JARVIS_ALLOWED_USER_IDS", str(FRIEND_ID))
    mod = _get_mod()

    upd = _text_update(FRIEND_ID, FRIEND_ID, "/swapbatch_go", "Artem_koval3")
    captured = _run(mod, upd, _orch_empty())

    go = [d for e, d in captured if e == "swapbatch_go"]
    assert go, f"expected swapbatch_go event, got {captured}"
    assert go[0].get("targets", 0) == 0


# ── self-suppression (audit_logger:192) survives the fix ─────────────────────


def test_admin_own_event_not_forwarded_even_with_targets(monkeypatch):
    """Admin's own swapbatch_go is NOT forwarded to admin, even with a count."""
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", str(ADMIN_ID))
    monkeypatch.setenv("JARVIS_AUDIT_FORWARD_ENABLED", "1")
    forwarded: list[str] = []
    monkeypatch.setattr(audit_logger, "_forward_to_admin", forwarded.append)

    audit_logger._maybe_forward({
        "event": "swapbatch_go",
        "user_id": ADMIN_ID,
        "username": "admin",
        "details": {"targets": 5},
    })
    assert forwarded == []


def test_friend_event_forwarded_with_real_count(monkeypatch):
    """A friend's swapbatch_go IS forwarded and shows the real target count."""
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", str(ADMIN_ID))
    monkeypatch.setenv("JARVIS_AUDIT_FORWARD_ENABLED", "1")
    forwarded: list[str] = []
    monkeypatch.setattr(audit_logger, "_forward_to_admin", forwarded.append)

    audit_logger._maybe_forward({
        "event": "swapbatch_go",
        "user_id": FRIEND_ID,
        "username": "Artem_koval3",
        "details": {"targets": 5},
    })
    assert forwarded, "friend event should be forwarded to admin"
    assert "5 targets" in forwarded[0]
