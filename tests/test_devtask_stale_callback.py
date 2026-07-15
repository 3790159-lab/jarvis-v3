# -*- coding: utf-8 -*-
"""Stale confirm-callbacks on already-terminal dev_task cards (Этап 1).

When the poll loop resumes after a restart it can re-deliver a merge/rollback
callback that was tapped in the kill→start window for a task that is *already*
terminal (merged / rolled_back / failed). Re-running the merge gate (full
regress, ~4-5 min) or re-removing a worktree for such a card is wasteful and
confusing. The card is terminal → politely acknowledge and ignore, never
re-execute.

$0, mocks only, no CC, no network, no real pytest/git.
"""
import importlib

from app.services.devtask.queue import (
    DevTaskQueue, STATUS_MERGED, STATUS_ROLLED_BACK, STATUS_FAILED,
    STATUS_AWAITING_REVIEW,
)

mod = importlib.import_module("tools.jarvis_smart_telegram_control")
ADMIN = "237616472"


def _seed(monkeypatch, tmp_path, status):
    q = DevTaskQueue(base_dir=tmp_path)
    tid = q.add("do X")
    q.set_status(tid, status, worktree="C:/wt/devtask-X", base_head="base1",
                 branch=f"devtask-{tid}")
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    return q, tid


# ── merge on a terminal card is politely ignored, never re-run ──────────────
def test_merge_on_merged_card_does_not_rerun_regress(monkeypatch, tmp_path):
    q, tid = _seed(monkeypatch, tmp_path, STATUS_MERGED)
    msgs = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: msgs.append(t))
    ran = {"full": False, "targeted": False, "did_merge": False}
    monkeypatch.setattr(mod, "_devtask_run_regress",
                        lambda *a, **k: ran.update(full=True) or {"ok": True, "text": ""})
    monkeypatch.setattr(mod, "_devtask_run_targeted",
                        lambda *a, **k: ran.update(targeted=True) or {"ok": True, "text": ""})
    monkeypatch.setattr(mod, "_devtask_do_merge",
                        lambda *a, **k: ran.update(did_merge=True))

    mod._devtask_merge(ADMIN, tid, mode="full")

    assert ran == {"full": False, "targeted": False, "did_merge": False}
    assert msgs and any("уже" in m.lower() for m in msgs)  # polite "already ..." note


def test_mergeforce_on_failed_card_does_not_merge(monkeypatch, tmp_path):
    q, tid = _seed(monkeypatch, tmp_path, STATUS_FAILED)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    did = {"merge": False}
    monkeypatch.setattr(mod, "_devtask_do_merge", lambda *a, **k: did.update(merge=True))

    mod._devtask_merge(ADMIN, tid, skip_regress=True)  # the [⚠️ force] path

    assert did["merge"] is False


# ── rollback on a terminal card is politely ignored, never re-removed ───────
def test_rollback_on_rolled_back_card_does_not_remove_worktree(monkeypatch, tmp_path):
    q, tid = _seed(monkeypatch, tmp_path, STATUS_ROLLED_BACK)
    msgs = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: msgs.append(t))
    from app.services.devtask import git_ops as g
    removed = {"x": False}
    monkeypatch.setattr(g, "remove_worktree", lambda *a, **k: removed.update(x=True))

    mod._devtask_rollback(ADMIN, tid)

    assert removed["x"] is False
    assert msgs and any("уже" in m.lower() for m in msgs)


# ── the guard must NOT block a legitimate merge of an awaiting-review card ───
def test_merge_on_awaiting_review_card_still_runs(monkeypatch, tmp_path):
    q, tid = _seed(monkeypatch, tmp_path, STATUS_AWAITING_REVIEW)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    ran = {"targeted": False}
    monkeypatch.setattr(mod, "_devtask_run_targeted",
                        lambda wt, base, tid=None: ran.update(targeted=True) or
                        {"ok": True, "text": "ok", "mode": "targeted"})
    monkeypatch.setattr(mod, "_devtask_do_merge", lambda *a, **k: None)
    from app.services.devtask import git_ops as g
    monkeypatch.setattr(g, "is_merged", lambda *a, **k: False)   # branch not yet in prod

    mod._devtask_merge(ADMIN, tid, mode="targeted")

    assert ran["targeted"] is True  # not blocked — card was reviewable
