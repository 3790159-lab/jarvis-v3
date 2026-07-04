# -*- coding: utf-8 -*-
"""Dev-task bot wiring (control module). $0, mocks only, no CC, no network."""
import importlib

from app.services.devtask.queue import DevTaskQueue, STATUS_RUNNING

mod = importlib.import_module("tools.jarvis_smart_telegram_control")
ADMIN = "237616472"


# ── Task 5: /dev_task admin-only + dispatch + single-flight ────────────────
def test_dev_task_is_admin_only_not_friend():
    assert "/dev_task" not in mod.FRIEND_ALLOWED_COMMANDS


def test_dev_task_enqueues_and_sends_confirm(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", DevTaskQueue(base_dir=tmp_path), raising=False)
    sent = {}
    monkeypatch.setattr(mod, "send_with_keyboard", lambda cid, t, kb: sent.setdefault("kb", (t, kb)))
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    mod.handle_command(ADMIN, "/dev_task", "add a foo command", {})
    t, kb = sent["kb"]
    assert "add a foo command" in t
    datas = [b["callback_data"] for row in kb for b in row]
    assert any(d.startswith("devtask:confirm:") for d in datas)
    assert any(d.startswith("devtask:cancel:") for d in datas)


def test_dev_task_rejects_when_active(monkeypatch, tmp_path):
    q = DevTaskQueue(base_dir=tmp_path)
    tid = q.add("busy")
    q.set_status(tid, STATUS_RUNNING)
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: sent.setdefault("kb", True))
    mod.handle_command(ADMIN, "/dev_task", "another", {})
    assert "активная" in sent["t"] and "kb" not in sent


def test_dev_task_empty_shows_usage(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", DevTaskQueue(base_dir=tmp_path), raising=False)
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))
    mod.handle_command(ADMIN, "/dev_task", "", {})
    assert "Использование" in sent["t"]


# ── Task 6: callbacks — regress gate, merge/mergeforce, rollback, details ───
from app.services.devtask.queue import STATUS_AWAITING_REVIEW, STATUS_MERGED, STATUS_ROLLED_BACK


def test_devtask_prefix_is_admin_only():
    assert "devtask:" not in mod.FRIEND_ALLOWED_CALLBACK_PREFIXES


def _seed_awaiting(monkeypatch, tmp_path, worktree="C:/wt/devtask-X"):
    q = DevTaskQueue(base_dir=tmp_path)
    tid = q.add("do X")
    q.set_status(tid, STATUS_AWAITING_REVIEW, worktree=worktree, base_head="base1",
                 branch=f"devtask-{tid}", report_path=str(tmp_path / f"{tid}" / "report.md"))
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    return q, tid


def test_merge_blocked_when_regress_worse(monkeypatch, tmp_path):
    q, tid = _seed_awaiting(monkeypatch, tmp_path)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    monkeypatch.setattr(mod, "_devtask_run_regress", lambda wt: {"ok": False, "text": "+3 fail"})
    merged = {"called": False}
    monkeypatch.setattr(mod, "_devtask_do_merge", lambda *a, **k: merged.update(called=True))
    mod._devtask_merge(ADMIN, tid, skip_regress=False)
    assert any("заблокирован" in s for s in sent) and merged["called"] is False
    assert q.get(tid)["status"] == STATUS_AWAITING_REVIEW  # unchanged


def test_merge_proceeds_when_regress_ok(monkeypatch, tmp_path):
    q, tid = _seed_awaiting(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_devtask_run_regress", lambda wt: {"ok": True, "text": "129==129"})
    order = []
    from app.services.devtask import git_ops as g, boot_watch as bw
    monkeypatch.setattr(g, "is_ff_clean", lambda *a, **k: True)
    monkeypatch.setattr(g, "prod_head", lambda *a, **k: "newsha")
    monkeypatch.setattr(g, "ff_merge", lambda *a, **k: order.append("merge"))
    monkeypatch.setattr(bw, "write_pending_restart", lambda *a, **k: order.append("pending"))
    monkeypatch.setattr(bw, "write_boot_watch", lambda *a, **k: order.append("watch"))
    monkeypatch.setattr(mod, "_devtask_restart", lambda cid: order.append("restart"))
    mod._devtask_merge(ADMIN, tid, skip_regress=False)
    assert order == ["merge", "pending", "watch", "restart"]   # restart LAST
    assert q.get(tid)["status"] == STATUS_MERGED


def test_mergeforce_skips_regress(monkeypatch, tmp_path):
    q, tid = _seed_awaiting(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    called = {"regress": False}
    monkeypatch.setattr(mod, "_devtask_run_regress", lambda wt: called.update(regress=True) or {"ok": True, "text": ""})
    from app.services.devtask import git_ops as g, boot_watch as bw
    monkeypatch.setattr(g, "is_ff_clean", lambda *a, **k: True)
    monkeypatch.setattr(g, "prod_head", lambda *a, **k: "n")
    monkeypatch.setattr(g, "ff_merge", lambda *a, **k: None)
    monkeypatch.setattr(bw, "write_pending_restart", lambda *a, **k: None)
    monkeypatch.setattr(bw, "write_boot_watch", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_devtask_restart", lambda cid: None)
    mod._devtask_merge(ADMIN, tid, skip_regress=True)
    assert called["regress"] is False and q.get(tid)["status"] == STATUS_MERGED


def test_merge_rejected_when_prod_moved(monkeypatch, tmp_path):
    q, tid = _seed_awaiting(monkeypatch, tmp_path)
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))
    monkeypatch.setattr(mod, "_devtask_run_regress", lambda wt: {"ok": True, "text": ""})
    from app.services.devtask import git_ops as g
    monkeypatch.setattr(g, "is_ff_clean", lambda *a, **k: False)
    restarted = {"x": False}
    monkeypatch.setattr(mod, "_devtask_restart", lambda cid: restarted.update(x=True))
    mod._devtask_merge(ADMIN, tid, skip_regress=True)
    assert "сдвин" in sent["t"].lower() and restarted["x"] is False
    assert q.get(tid)["status"] == STATUS_AWAITING_REVIEW


def test_rollback_removes_worktree(monkeypatch, tmp_path):
    q, tid = _seed_awaiting(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    removed = {"x": False}
    from app.services.devtask import git_ops as g
    monkeypatch.setattr(g, "remove_worktree", lambda *a, **k: removed.update(x=True))
    mod._devtask_rollback(ADMIN, tid)
    assert removed["x"] is True and q.get(tid)["status"] == STATUS_ROLLED_BACK


def test_details_sends_report(monkeypatch, tmp_path):
    q, tid = _seed_awaiting(monkeypatch, tmp_path)
    rp = tmp_path / tid / "report.md"
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text("VERDICT: READY\nall green", encoding="utf-8")
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))
    mod._devtask_details(ADMIN, tid)
    assert "READY" in sent["t"]


def test_confirm_creates_worktree_and_starts_run(monkeypatch, tmp_path):
    from app.services.devtask.queue import STATUS_RUNNING
    q = DevTaskQueue(base_dir=tmp_path)
    tid = q.add("build X")
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    from app.services.devtask import git_ops as g
    made = {}
    monkeypatch.setattr(g, "prod_head", lambda *a, **k: "base1")
    monkeypatch.setattr(g, "create_worktree", lambda tid_, base, **k: made.setdefault("wt", f"C:/wt/devtask-{tid_}"))
    started = {"x": False}
    # run body injected: do not spawn real CC
    monkeypatch.setattr(mod, "_devtask_run_body", lambda cid, t: started.update(x=True))

    class _Immediate:
        def __init__(self, *a, **k):
            self._t = k.get("target"); self._args = k.get("args", ())
        def start(self):
            self._t(*self._args)
    monkeypatch.setattr(mod.threading, "Thread", _Immediate)
    mod._devtask_confirm(ADMIN, tid)
    assert "wt" in made and started["x"] is True
    assert q.get(tid)["status"] == STATUS_RUNNING and q.get(tid)["base_head"] == "base1"


# ── Task 7: boot reconciliation (post-restart report + interrupted-run) ─────
from app.services.devtask import boot_watch as _bw_mod
from app.services.devtask.queue import STATUS_FAILED, STATUS_RUNNING as _RUN


def test_boot_reconcile_pending_restart_single_shot(monkeypatch, tmp_path):
    q = DevTaskQueue(base_dir=tmp_path)
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    _bw_mod.write_pending_restart(tmp_path, "T1", "old", "new")
    _bw_mod.write_boot_watch(tmp_path, "T1", "old", "new", now=0)
    sent = []
    mod._devtask_boot_reconcile(base_dir=tmp_path, send_fn=lambda t: sent.append(t))
    assert any("смерджена" in s for s in sent)
    assert _bw_mod.read_pending_restart(tmp_path) is None          # single-shot
    assert not (tmp_path / "boot_watch.json").exists()             # cleared on healthy boot
    sent.clear()
    mod._devtask_boot_reconcile(base_dir=tmp_path, send_fn=lambda t: sent.append(t))
    assert sent == []                                              # no repeat


def test_boot_reconcile_marks_interrupted_running_failed(monkeypatch, tmp_path):
    q = DevTaskQueue(base_dir=tmp_path)
    tid = q.add("x")
    q.set_status(tid, _RUN)
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    sent = []
    mod._devtask_boot_reconcile(base_dir=tmp_path, send_fn=lambda t: sent.append(t))
    assert q.get(tid)["status"] == STATUS_FAILED
    assert any("прервана" in s for s in sent)
