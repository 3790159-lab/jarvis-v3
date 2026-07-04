# -*- coding: utf-8 -*-
"""Fix-arc: callback-exception visibility + confirm worktree-failure handling.

$0, mocks only. Regression for the silent-swallow that made /dev_task fail
invisibly (branch created, no worktree, task stuck 'queued', zero feedback).
"""
import importlib

from app.services.devtask.queue import DevTaskQueue, STATUS_FAILED

mod = importlib.import_module("tools.jarvis_smart_telegram_control")
ADMIN = "237616472"


# ── Fix (а)-1: process_update must LOG a callback exception, not swallow it ──
def test_callback_exception_is_logged(monkeypatch):
    logged = {}

    def boom(cq, state):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(mod, "handle_callback_query", boom)
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_is_member_id", lambda uid: True)
    monkeypatch.setattr(mod, "load_state", lambda: {})
    monkeypatch.setattr(mod.logger, "exception", lambda *a, **k: logged.setdefault("x", True))
    upd = {"callback_query": {"id": "1", "from": {"id": 237616472},
                              "message": {"message_id": 5, "chat": {"id": 237616472}},
                              "data": "devtask:confirm:X"}}
    mod.process_update(upd)
    assert logged.get("x") is True   # the swallowed exception is now logged


# ── Fix (а)-2: confirm worktree failure -> task failed + explicit send() ────
def test_confirm_worktree_failure_marks_failed_and_notifies(monkeypatch, tmp_path):
    q = DevTaskQueue(base_dir=tmp_path)
    tid = q.add("do X")
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    from app.services.devtask import git_ops as g
    monkeypatch.setattr(g, "prod_head", lambda *a, **k: "base1")

    def boom(*a, **k):
        raise RuntimeError("git worktree add failed: fatal: something")

    monkeypatch.setattr(g, "create_worktree", boom)
    started = {"x": False}
    monkeypatch.setattr(mod, "_devtask_run_body", lambda *a, **k: started.update(x=True))

    mod._devtask_confirm(ADMIN, tid)

    assert q.get(tid)["status"] == STATUS_FAILED            # not stuck 'queued'
    assert any("git worktree" in s for s in sent)           # explicit error text to admin
    assert started["x"] is False                            # CC run never started
