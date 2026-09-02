# -*- coding: utf-8 -*-
"""DEV-96: a dev_task whose worktree got deleted out-of-band must not hold the
single-flight lock forever, [Детали] must not lie about a report that lives
nowhere reachable, and [Откат] on such a card must not crash.

$0, mocks only, no CC, no network, no real pytest/git.
"""
import importlib

from app.services.devtask.queue import (
    DevTaskQueue, STATUS_AWAITING_REVIEW, STATUS_ORPHANED, STATUS_RUNNING,
)

mod = importlib.import_module("tools.jarvis_smart_telegram_control")
ADMIN = "237616472"


def _seed(monkeypatch, tmp_path, status, worktree):
    q = DevTaskQueue(base_dir=tmp_path / "dev_tasks")
    tid = q.add("do X")
    q.set_status(tid, status, worktree=worktree, base_head="base1",
                 branch=f"devtask-{tid}")
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    return q, tid


# ── dispatch: a missing worktree is reaped and does NOT block a new task ────
def test_dispatch_reaps_missing_worktree_and_proceeds(monkeypatch, tmp_path):
    gone = str(tmp_path / "gone-worktree")            # never created -> missing
    q, tid = _seed(monkeypatch, tmp_path, STATUS_AWAITING_REVIEW, gone)
    msgs = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: msgs.append(t))
    kb = []
    monkeypatch.setattr(mod, "send_with_keyboard",
                        lambda cid, t, buttons: kb.append((t, buttons)))

    mod._devtask_dispatch(ADMIN, "new task")

    assert q.get(tid)["status"] == STATUS_ORPHANED               # unlocked
    assert any(tid in m and "потеряла worktree" in m for m in msgs)  # one notice
    assert len(kb) == 1 and "new task" in kb[0][0]                # new task proceeded


def test_dispatch_sends_exactly_one_orphan_notice(monkeypatch, tmp_path):
    gone = str(tmp_path / "gone-worktree")
    _seed(monkeypatch, tmp_path, STATUS_RUNNING, gone)
    msgs = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: msgs.append(t))
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: None)

    mod._devtask_dispatch(ADMIN, "new task")

    orphan_notices = [m for m in msgs if "потеряла worktree" in m]
    assert len(orphan_notices) == 1


# ── dispatch: a REAL, still-present worktree keeps blocking as before ───────
def test_dispatch_still_blocks_on_live_worktree(monkeypatch, tmp_path):
    live = tmp_path / "live-worktree"
    live.mkdir()
    q, tid = _seed(monkeypatch, tmp_path, STATUS_AWAITING_REVIEW, str(live))
    msgs = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: msgs.append(t))
    kb = []
    monkeypatch.setattr(mod, "send_with_keyboard",
                        lambda cid, t, buttons: kb.append((t, buttons)))

    mod._devtask_dispatch(ADMIN, "new task")

    assert q.get(tid)["status"] == STATUS_AWAITING_REVIEW   # untouched, still active
    assert kb == []                                          # new task NOT started
    assert any("активная dev-задача" in m for m in msgs)


# ── [Детали] on an orphaned card: honest, no crash ───────────────────────────
def test_details_on_orphaned_says_no_report_plainly(monkeypatch, tmp_path):
    gone = str(tmp_path / "gone-worktree")
    q, tid = _seed(monkeypatch, tmp_path, STATUS_ORPHANED, gone)
    msgs = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: msgs.append(t))
    monkeypatch.setattr(mod, "_devtask_state_dir", lambda: tmp_path / "dev_tasks")

    mod._devtask_details(ADMIN, tid)

    assert len(msgs) == 1
    assert "отчёта нет" in msgs[0].lower()
    assert tid in msgs[0]
    # "что осталось" — orphaned status + the still-known state must be visible,
    # not just a generic "report" header shared with the non-orphaned path.
    assert "orphaned" in msgs[0].lower()
    assert gone in msgs[0]                   # the (now-gone) worktree path itself
    assert "do X" in msgs[0]                 # the task description


def test_details_on_orphaned_shows_live_report_when_it_was_persisted(monkeypatch, tmp_path):
    # A task can reach awaiting_review (report already copied to the live tree,
    # DEV-96 persist_report) and THEN have its worktree vanish -> still orphaned,
    # but the report is not lost, so Details must show it.
    gone = str(tmp_path / "gone-worktree")
    q, tid = _seed(monkeypatch, tmp_path, STATUS_ORPHANED, gone)
    live_dir = tmp_path / "dev_tasks" / tid
    live_dir.mkdir(parents=True)
    (live_dir / "report.md").write_text("VERDICT: READY", encoding="utf-8")
    msgs = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: msgs.append(t))
    monkeypatch.setattr(mod, "_devtask_state_dir", lambda: tmp_path / "dev_tasks")

    mod._devtask_details(ADMIN, tid)

    assert len(msgs) == 1
    assert "VERDICT: READY" in msgs[0]
    assert "orphaned" in msgs[0].lower()      # still flagged as orphaned, not hidden


# ── [Откат] on an orphaned card: never crashes, never lies about git ops ────
def test_rollback_on_orphaned_does_not_call_git_ops(monkeypatch, tmp_path):
    gone = str(tmp_path / "gone-worktree")
    q, tid = _seed(monkeypatch, tmp_path, STATUS_ORPHANED, gone)
    msgs = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: msgs.append(t))
    from app.services.devtask import git_ops as g
    removed = {"x": False}
    monkeypatch.setattr(g, "remove_worktree", lambda *a, **k: removed.update(x=True))

    mod._devtask_rollback(ADMIN, tid)

    assert removed["x"] is False                       # nothing to remove, don't try
    assert q.get(tid)["status"] == "rolled_back"
    assert msgs                                          # no crash, admin informed


def test_rollback_on_orphaned_survives_even_if_git_ops_would_raise(monkeypatch, tmp_path):
    # Belt-and-braces: even if some future change wired git_ops back in for
    # orphaned cards, a raise there must not crash the handler.
    gone = str(tmp_path / "gone-worktree")
    q, tid = _seed(monkeypatch, tmp_path, STATUS_ORPHANED, gone)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    from app.services.devtask import git_ops as g
    monkeypatch.setattr(g, "remove_worktree",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    mod._devtask_rollback(ADMIN, tid)   # must not raise

    assert q.get(tid)["status"] == "rolled_back"
