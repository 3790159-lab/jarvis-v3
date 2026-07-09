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


def test_confirm_sets_running_and_starts_run_body(monkeypatch, tmp_path):
    # Confirm no longer builds the worktree (heavy ~30-50s detached checkout runs
    # in the daemon thread now); it just claims 'running' and spawns run_body.
    from app.services.devtask.queue import STATUS_RUNNING
    q = DevTaskQueue(base_dir=tmp_path)
    tid = q.add("build X")
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_devtask_free_gb", lambda *a, **k: 999.0)  # plenty of disk
    started = {"x": False}
    monkeypatch.setattr(mod, "_devtask_run_body", lambda cid, t: started.update(x=True))

    class _Immediate:
        def __init__(self, *a, **k):
            self._t = k.get("target"); self._args = k.get("args", ())
        def start(self):
            self._t(*self._args)
    monkeypatch.setattr(mod.threading, "Thread", _Immediate)
    mod._devtask_confirm(ADMIN, tid)
    assert started["x"] is True
    assert q.get(tid)["status"] == STATUS_RUNNING
    assert q.get(tid)["base_head"] is None          # worktree not built in confirm


def test_confirm_double_delivery_starts_run_body_once(monkeypatch, tmp_path):
    # Two bot pollers (separate processes) can each get the same confirm callback
    # in the offset-overlap window, so BOTH pass the status pre-check while the
    # task is still 'queued'. The atomic claim must be the barrier that lets
    # exactly one through — otherwise two worktrees race ('branch already exists').
    q = DevTaskQueue(base_dir=tmp_path)
    tid = q.add("build X")
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_devtask_free_gb", lambda *a, **k: 999.0)
    # Force the pre-check to see 'queued' on both calls (simulates the race window
    # before either handler has persisted 'running'); claim()'s O_EXCL marker is
    # then the ONLY thing that can serialise them.
    real_get = q.get
    monkeypatch.setattr(q, "get", lambda t: {**real_get(t), "status": "queued"})
    starts = []
    monkeypatch.setattr(mod, "_devtask_run_body", lambda cid, t: starts.append(t))

    class _Immediate:
        def __init__(self, *a, **k):
            self._t = k.get("target"); self._args = k.get("args", ())
        def start(self):
            self._t(*self._args)
    monkeypatch.setattr(mod.threading, "Thread", _Immediate)
    mod._devtask_confirm(ADMIN, tid)
    mod._devtask_confirm(ADMIN, tid)
    assert len(starts) == 1                          # exactly one worktree run


def test_confirm_message_states_expected_duration(monkeypatch, tmp_path):
    # The start message must tell the admin roughly how long to wait, so they
    # don't sit staring at the chat wondering if it hung.
    q = DevTaskQueue(base_dir=tmp_path)
    tid = q.add("build X")
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    monkeypatch.setattr(mod, "_devtask_free_gb", lambda *a, **k: 999.0)
    monkeypatch.setattr(mod, "_devtask_run_body", lambda *a, **k: None)

    class _Immediate:
        def __init__(self, *a, **k):
            self._t = k.get("target"); self._args = k.get("args", ())
        def start(self):
            self._t(*self._args)
    monkeypatch.setattr(mod.threading, "Thread", _Immediate)
    mod._devtask_confirm(ADMIN, tid)
    assert any("~10-30 мин" in s for s in sent)


def test_confirm_refuses_when_disk_low(monkeypatch, tmp_path):
    # Pre-flight guard: a worktree is a full ~2 GB checkout. With too little free
    # disk, refuse immediately with an honest message — never attempt the checkout
    # (which fills the disk and then can't even persist its own failure).
    q = DevTaskQueue(base_dir=tmp_path)
    tid = q.add("build X")
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    monkeypatch.setattr(mod, "_devtask_free_gb", lambda *a, **k: 1.2)   # below default 4
    started = {"x": False}
    monkeypatch.setattr(mod, "_devtask_run_body", lambda *a, **k: started.update(x=True))
    mod._devtask_confirm(ADMIN, tid)
    assert started["x"] is False                    # checkout never attempted
    assert q.get(tid)["status"] == "queued"         # not claimed running (retryable)
    assert any("места" in s for s in sent)          # honest disk message


def test_run_body_worktree_failure_notifies_before_persist(monkeypatch, tmp_path):
    # On a full disk the failure-persist itself raises OSError. The admin
    # notification MUST NOT depend on that write — send() comes first, and a
    # failing set_status can never swallow the alert (the silent-death bug).
    from app.services.devtask.queue import STATUS_RUNNING
    q = DevTaskQueue(base_dir=tmp_path)
    tid = q.add("do X")
    q.set_status(tid, STATUS_RUNNING)
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    from app.services.devtask import git_ops as g, runner as r
    monkeypatch.setattr(g, "prod_head", lambda *a, **k: "base1")
    monkeypatch.setattr(g, "create_worktree",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full mid-checkout")))
    monkeypatch.setattr(r, "run", lambda **k: (_ for _ in ()).throw(AssertionError("CC must not run")))

    def boom_persist(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(q, "set_status", boom_persist)
    mod._devtask_run_body(ADMIN, tid)               # must NOT raise out of the thread
    assert any("worktree" in s for s in sent)       # admin alerted despite persist failure


def test_boot_reconcile_removes_merged_worktree(monkeypatch, tmp_path):
    # Auto-cleanup: once a healthy boot confirms the merge (pending_restart found),
    # the merged task's worktree is removed — otherwise merged worktrees pile up
    # (this is what filled the disk). Removal happens ONLY after healthy boot.
    q = DevTaskQueue(base_dir=tmp_path)
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    _bw_mod.write_pending_restart(tmp_path, "T7", "old", "new")
    _bw_mod.write_boot_watch(tmp_path, "T7", "old", "new", now=0)
    removed = []
    from app.services.devtask import git_ops as g
    monkeypatch.setattr(g, "remove_worktree", lambda tid, *a, **k: removed.append(tid))
    mod._devtask_boot_reconcile(base_dir=tmp_path, send_fn=lambda t: None)
    assert removed == ["T7"]                         # merged worktree cleaned up


def test_run_body_report_path_is_under_worktree(monkeypatch, tmp_path):
    # Contract: CC writes the report relative to ITS cwd (the worktree). The
    # runner must therefore look for it UNDER the worktree, not under the bot's
    # cwd — otherwise even a perfect CC yields no_report.
    from app.services.devtask.queue import STATUS_RUNNING
    q = DevTaskQueue(base_dir=tmp_path)
    tid = q.add("x")
    q.set_status(tid, STATUS_RUNNING)
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: None)
    from app.services.devtask import git_ops as g, runner as r
    monkeypatch.setattr(g, "prod_head", lambda *a, **k: "base1")
    wt = str(tmp_path / "wt")
    monkeypatch.setattr(g, "create_worktree", lambda *a, **k: wt)
    seen = {}
    monkeypatch.setattr(r, "run", lambda **k: seen.update(k) or {"status": "failed", "reason": "no_result"})
    mod._devtask_run_body(ADMIN, tid)
    rp = seen["report_path"].replace("\\", "/")
    assert rp.startswith(wt.replace("\\", "/"))          # under the worktree, not bot cwd
    assert rp.endswith("state/dev_tasks/%s/report.md" % tid)


def test_run_body_creates_worktree_then_runs(monkeypatch, tmp_path):
    from app.services.devtask.queue import STATUS_RUNNING, STATUS_AWAITING_REVIEW
    q = DevTaskQueue(base_dir=tmp_path)
    tid = q.add("build X")
    q.set_status(tid, STATUS_RUNNING)
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: None)
    from app.services.devtask import git_ops as g, runner as r
    monkeypatch.setattr(g, "prod_head", lambda *a, **k: "base1")
    monkeypatch.setattr(g, "create_worktree", lambda tid_, base, **k: f"C:/wt/devtask-{tid_}")
    monkeypatch.setattr(r, "run", lambda **k: {"status": "awaiting_review", "session_id": "s", "cost": 0.1})
    mod._devtask_run_body(ADMIN, tid)
    assert q.get(tid)["worktree"] == f"C:/wt/devtask-{tid}"
    assert q.get(tid)["base_head"] == "base1"
    assert q.get(tid)["status"] == STATUS_AWAITING_REVIEW


# ── Task 7: boot reconciliation (post-restart report + interrupted-run) ─────
from app.services.devtask import boot_watch as _bw_mod
from app.services.devtask.queue import STATUS_FAILED, STATUS_RUNNING as _RUN


def test_boot_reconcile_pending_restart_single_shot(monkeypatch, tmp_path):
    q = DevTaskQueue(base_dir=tmp_path)
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    from app.services.devtask import git_ops as _g
    monkeypatch.setattr(_g, "remove_worktree", lambda *a, **k: None)  # no real git
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
