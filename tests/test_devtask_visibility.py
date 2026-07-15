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
def test_run_body_worktree_failure_marks_failed_and_notifies(monkeypatch, tmp_path):
    # Worktree setup now runs INSIDE the daemon thread (_devtask_run_body), so a
    # failure there must still: mark the task failed (not stuck) + send an
    # explicit error to the admin + never launch CC.
    from app.services.devtask.queue import STATUS_RUNNING
    q = DevTaskQueue(base_dir=tmp_path)
    tid = q.add("do X")
    q.set_status(tid, STATUS_RUNNING)
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: None)
    from app.services.devtask import git_ops as g, runner as r
    monkeypatch.setattr(g, "prod_head", lambda *a, **k: "base1")

    def boom(*a, **k):
        raise RuntimeError("git worktree add failed: fatal: something")

    monkeypatch.setattr(g, "create_worktree", boom)
    spawned = {"x": False}
    monkeypatch.setattr(r, "spawn_launcher", lambda t, **k: spawned.update(x=True) or 111)

    mod._devtask_run_body(ADMIN, tid)

    assert q.get(tid)["status"] == STATUS_FAILED            # not stuck
    assert any("git worktree" in s for s in sent)           # explicit error text to admin
    assert spawned["x"] is False                            # CC launcher never spawned


# ── Fix (WinError 2 arc): launcher-spawn failure must LOG a traceback, not
# only store str ────────────────────────────────────────────────────────────
def test_run_body_launcher_spawn_failure_is_logged(monkeypatch, tmp_path):
    # Worktree setup succeeds, but spawning the detached launcher raises (e.g.
    # WinError 2 / powershell missing). The handler must set failed + notify
    # AND logger.exception — without the log, a spawn failure left no
    # traceback in jarvis_bot.log (the bug that made the original WinError 2
    # hard to diagnose; DEV-11 moved the spawn from r.run to r.spawn_launcher).
    from app.services.devtask.queue import STATUS_RUNNING
    q = DevTaskQueue(base_dir=tmp_path)
    tid = q.add("do X")
    q.set_status(tid, STATUS_RUNNING)
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: None)
    from app.services.devtask import git_ops as g, runner as r
    monkeypatch.setattr(g, "prod_head", lambda *a, **k: "base1")
    monkeypatch.setattr(g, "create_worktree", lambda *a, **k: str(tmp_path / "wt"))

    def boom(*a, **k):
        raise FileNotFoundError(2, "Не удается найти указанный файл")

    monkeypatch.setattr(r, "spawn_launcher", boom)
    logged = {}
    monkeypatch.setattr(mod.logger, "exception",
                        lambda *a, **k: logged.setdefault("x", True))

    mod._devtask_run_body(ADMIN, tid)

    assert q.get(tid)["status"] == STATUS_FAILED            # not stuck
    assert logged.get("x") is True                          # traceback logged


# ── Cost lever (2026-07-10): DEVTASK_AUTH_MODE gating ───────────────────────
def test_run_body_api_mode_blocks_on_exhausted_budget(monkeypatch, tmp_path):
    from app.services.devtask.queue import STATUS_RUNNING
    q = DevTaskQueue(base_dir=tmp_path); tid = q.add("do X"); q.set_status(tid, STATUS_RUNNING)
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    monkeypatch.setenv("DEVTASK_AUTH_MODE", "api")
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    from app.services.devtask import git_ops as g, preflight as pf
    made = {"wt": False}
    monkeypatch.setattr(g, "prod_head", lambda *a, **k: "base1")
    monkeypatch.setattr(g, "create_worktree", lambda *a, **k: made.update(wt=True) or "x")
    monkeypatch.setattr(pf, "preflight_budget_check", lambda *a, **k: {"ok": False, "reason": "budget"})
    mod._devtask_run_body(ADMIN, tid)
    assert q.get(tid)["status"] == STATUS_FAILED
    assert any("preflight" in s for s in sent)      # gated in api mode
    assert made["wt"] is False                      # blocked before worktree


def test_run_body_subscription_skips_preflight(monkeypatch, tmp_path):
    from app.services.devtask.queue import STATUS_RUNNING
    q = DevTaskQueue(base_dir=tmp_path); tid = q.add("do X"); q.set_status(tid, STATUS_RUNNING)
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    monkeypatch.delenv("DEVTASK_AUTH_MODE", raising=False)   # default = subscription
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: None)
    from app.services.devtask import git_ops as g, preflight as pf, runner as r
    monkeypatch.setattr(g, "prod_head", lambda *a, **k: "base1")
    monkeypatch.setattr(g, "create_worktree", lambda *a, **k: str(tmp_path / "wt"))

    def canary_boom(*a, **k):
        raise AssertionError("preflight must NOT run in subscription mode")
    monkeypatch.setattr(pf, "preflight_credit_check", canary_boom)
    monkeypatch.setattr(pf, "preflight_budget_check", canary_boom)
    monkeypatch.setattr(r, "spawn_launcher", lambda t, **k: 555)
    mod._devtask_run_body(ADMIN, tid)
    item = q.get(tid)
    assert item["status"] == STATUS_RUNNING                  # reached spawn, canary never tripped
    assert item["cc_pid"] == 555


def test_poll_active_rate_limit_sends_quota_hint_not_generic(monkeypatch, tmp_path):
    # DEV-11 moved the CC-completion handling (incl. this rate-limit message)
    # out of _devtask_run_body into the heartbeat-driven _devtask_poll_active,
    # since CC now runs detached and the bot never blocks waiting on it.
    q = DevTaskQueue(base_dir=tmp_path)
    tid = q.add("do X")
    q.set_status(tid, "running", worktree=str(tmp_path / "wt"), cc_pid=4242)
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    from app.services.devtask import runner as r
    monkeypatch.setattr(r, "read_cc_result", lambda p: {
        "status": "failed", "reason": "cc_error: Claude AI usage limit reached", "cost": 0.02})
    mod._devtask_poll_active()
    assert q.get(tid)["status"] == STATUS_FAILED
    assert any("Max-квота" in s for s in sent)              # tailored quota message
    assert any("DEVTASK_AUTH_MODE=api" in s for s in sent)  # honest switch hint, no silent fallback
