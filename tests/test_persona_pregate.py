# -*- coding: utf-8 -*-
"""Money hole (a): pre-spend check_limit gate on persona generation commands.

$0, mocks only, zero real API. Each command must run check_limit BEFORE the
paid daemon thread is launched; a denial => "🚫", no thread, no cost record.
The existing fact-cost record on success is NOT touched (no double-charge).
Pattern mirrors Арка 1 T6 (create_persona/train_lora gates in same module).
"""
import app.handlers.persona_handler as ph


def _thread_spy(started):
    class _T:
        def __init__(self, *a, **k):
            pass

        def start(self):
            started.append(1)
    return _T


def _deny(*_a, **_k):
    return (False, "дневной лимит $5 исчерпан")


def _allow_capturing(store):
    def _cl(user_id, *, estimated_usd):
        store["est"] = estimated_usd
        store["uid"] = user_id
        return (True, "")
    return _cl


# ── Task 1: /persona_photo ────────────────────────────────────────────────
def test_persona_photo_pregate_blocks_before_paid_thread(monkeypatch):
    sent = []
    started = []
    monkeypatch.setattr(ph, "check_limit", _deny)
    monkeypatch.setattr(ph, "_safe_send", lambda cid, t, *a, **k: sent.append(t))
    monkeypatch.setattr(ph.threading, "Thread", _thread_spy(started))
    ph.handle_persona_photo(237616472, "mypersona a cool prompt")
    assert not started                       # paid daemon thread NOT launched
    assert any("🚫" in s for s in sent)       # refusal shown


def test_persona_photo_pregate_allows_and_uses_env_est(monkeypatch):
    store = {}
    started = []
    monkeypatch.setattr(ph, "check_limit", _allow_capturing(store))
    monkeypatch.setattr(ph, "_safe_send", lambda *a, **k: None)
    monkeypatch.setattr(ph.threading, "Thread", _thread_spy(started))
    ph.handle_persona_photo(237616472, "mypersona a cool prompt")
    assert started                           # paid path entered
    assert store["est"] == 0.05              # PERSONA_PHOTO_USD default
    assert store["uid"] == 237616472


# ── Task 2: /persona_video + /persona_video_redo (via ctl dispatch) ───────
# The gate runs at the very top of _persona_video_dispatch — BEFORE the heavy
# persona_video_handler import (which pulls `replicate`, absent in the test
# env). So a denial returns fast without importing the paid engine; that is the
# money-critical path and the one we assert. (Bare /persona_video = free help,
# not gated — asserted below.)
def _blocking_dispatch(monkeypatch, mod, store, sent, **kwargs):
    def _cl(uid, *, estimated_usd):
        store["est"] = estimated_usd
        return (False, "дневной лимит исчерпан")
    monkeypatch.setattr(mod, "_check_limit", _cl)
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    mod._persona_video_dispatch(237616472, "mypersona a prompt", **kwargs)


def test_persona_video_pregate_blocks_and_uses_est(monkeypatch):
    import tools.jarvis_smart_telegram_control as mod
    store, sent = {}, []
    _blocking_dispatch(monkeypatch, mod, store, sent)
    assert any("🚫" in s for s in sent)      # refusal (no paid engine imported)
    assert store["est"] == 0.40              # PERSONA_VIDEO_USD default


def test_persona_video_redo_also_gated(monkeypatch):
    import tools.jarvis_smart_telegram_control as mod
    store, sent = {}, []
    _blocking_dispatch(monkeypatch, mod, store, sent, command="redo")
    assert any("🚫" in s for s in sent)
    assert store["est"] == 0.40


def test_persona_video_bare_help_not_gated(monkeypatch):
    # Bare /persona_video (empty query) must NOT call check_limit (free help).
    import tools.jarvis_smart_telegram_control as mod
    called = []
    monkeypatch.setattr(mod, "_check_limit",
                        lambda *a, **k: called.append(1) or (True, ""))
    # help path imports the handler (needs `replicate`); we only assert the gate
    # is not consulted, so stop execution right after via a sentinel send.
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    try:
        mod._persona_video_dispatch(237616472, "   ")
    except Exception:
        pass  # downstream help import may fail in test env — irrelevant here
    assert called == []                      # check_limit NOT consulted for help
