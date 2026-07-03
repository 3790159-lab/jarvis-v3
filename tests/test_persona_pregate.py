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
