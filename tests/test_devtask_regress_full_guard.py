# -*- coding: utf-8 -*-
"""Full-regress guard (Этап 1): while the full regress is sick (OOM/timeout),
the [✅ Мердж] button must not fire it blindly. Under REGRESS_FULL_GUARD=1
(default on) tapping [✅ Мердж] shows a warning + [Да, полный]/[Отмена] confirm
instead of launching the full regress. UX-only: the regress mechanics are
untouched. $0, mocks only, no CC, no network, no real pytest/git.
"""
import importlib

mod = importlib.import_module("tools.jarvis_smart_telegram_control")
ADMIN = "237616472"


def _cq(data):
    return {
        "id": "cq1", "from": {"id": int(ADMIN)},
        "message": {"chat": {"id": int(ADMIN)}, "message_id": 1},
        "data": data,
    }


# ── env flag: default ON, "0" turns it off ──────────────────────────────────
def test_full_guard_on_by_default(monkeypatch):
    monkeypatch.delenv("REGRESS_FULL_GUARD", raising=False)
    assert mod._regress_full_guard_on() is True


def test_full_guard_off_when_zero(monkeypatch):
    monkeypatch.setenv("REGRESS_FULL_GUARD", "0")
    assert mod._regress_full_guard_on() is False


# ── confirm keyboard exposes both [Да, полный] and [Отмена] ─────────────────
def test_full_guard_keyboard_has_confirm_and_cancel():
    kb = mod._devtask_full_guard_keyboard("T1")
    datas = [b["callback_data"] for row in kb for b in row]
    assert "devtask:mergefull:T1" in datas
    assert "devtask:mergecancel:T1" in datas


# ── guard ON: [✅ Мердж] warns instead of running the full regress ──────────
def test_merge_tap_warns_when_guard_on(monkeypatch):
    monkeypatch.setenv("REGRESS_FULL_GUARD", "1")
    called = {"merge": False}
    monkeypatch.setattr(mod, "_devtask_merge",
                        lambda *a, **k: called.update(merge=True))

    class _Immediate:
        def __init__(self, *a, **k):
            self._t = k.get("target"); self._args = k.get("args", ()); self._kw = k.get("kwargs", {})
        def start(self):
            self._t(*self._args, **self._kw)
    monkeypatch.setattr(mod.threading, "Thread", _Immediate)
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)
    seen = {}
    monkeypatch.setattr(mod, "send_with_keyboard",
                        lambda cid, text, kb, *a, **k: seen.update(text=text, kb=kb))

    mod.handle_callback_query(_cq("devtask:merge:T1"), {})

    assert called["merge"] is False                       # full regress NOT launched
    assert "нестабил" in seen["text"] and "полный" in seen["text"].lower()
    datas = [b["callback_data"] for row in seen["kb"] for b in row]
    assert "devtask:mergefull:T1" in datas and "devtask:mergecancel:T1" in datas


# ── guard OFF: [✅ Мердж] runs the full merge as before ─────────────────────
def test_merge_tap_runs_full_when_guard_off(monkeypatch):
    monkeypatch.setenv("REGRESS_FULL_GUARD", "0")
    got = {}
    monkeypatch.setattr(mod, "_devtask_merge",
                        lambda cid, t, **k: got.update(tid=t, kwargs=k))

    class _Immediate:
        def __init__(self, *a, **k):
            self._t = k.get("target"); self._args = k.get("args", ()); self._kw = k.get("kwargs", {})
        def start(self):
            self._t(*self._args, **self._kw)
    monkeypatch.setattr(mod.threading, "Thread", _Immediate)
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)

    mod.handle_callback_query(_cq("devtask:merge:T1"), {})

    assert got["tid"] == "T1"
    # full merge = no targeted/skip override
    assert got["kwargs"].get("mode", "full") == "full"
    assert got["kwargs"].get("skip_regress", False) is False


# ── [Да, полный] confirm actually runs the full merge ──────────────────────
def test_mergefull_confirm_runs_full_merge(monkeypatch):
    monkeypatch.setenv("REGRESS_FULL_GUARD", "1")   # guard on; confirm bypasses it
    got = {}
    monkeypatch.setattr(mod, "_devtask_merge",
                        lambda cid, t, **k: got.update(tid=t, kwargs=k))

    class _Immediate:
        def __init__(self, *a, **k):
            self._t = k.get("target"); self._args = k.get("args", ()); self._kw = k.get("kwargs", {})
        def start(self):
            self._t(*self._args, **self._kw)
    monkeypatch.setattr(mod.threading, "Thread", _Immediate)
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)

    mod.handle_callback_query(_cq("devtask:mergefull:T1"), {})

    assert got["tid"] == "T1"
    assert got["kwargs"].get("mode", "full") == "full"
    assert got["kwargs"].get("skip_regress", False) is False


# ── [Отмена] cancels without running any merge ──────────────────────────────
def test_mergecancel_does_not_merge(monkeypatch):
    called = {"merge": False}
    monkeypatch.setattr(mod, "_devtask_merge",
                        lambda *a, **k: called.update(merge=True))
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))

    mod.handle_callback_query(_cq("devtask:mergecancel:T1"), {})

    assert called["merge"] is False
    assert any("тмен" in s for s in sent)   # "Отменено"
