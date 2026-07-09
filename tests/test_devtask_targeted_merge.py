# -*- coding: utf-8 -*-
"""Targeted-test merge mode for the dev_task pipeline (control wiring).

A conscious bypass of the full regress before merge: run only the tests that map
to the branch diff. Full regress stays the DEFAULT — this only adds the bypass.
$0, mocks only, no CC, no network, no real pytest/git.
"""
import importlib

from app.services.devtask.queue import (
    DevTaskQueue, STATUS_AWAITING_REVIEW, STATUS_MERGED,
)

mod = importlib.import_module("tools.jarvis_smart_telegram_control")
ADMIN = "237616472"


def _seed_awaiting(monkeypatch, tmp_path, worktree="C:/wt/devtask-X", base="base1"):
    q = DevTaskQueue(base_dir=tmp_path)
    tid = q.add("do X")
    q.set_status(tid, STATUS_AWAITING_REVIEW, worktree=worktree, base_head=base,
                 branch=f"devtask-{tid}", report_path=str(tmp_path / tid / "report.md"))
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    return q, tid


# ── review keyboard offers the targeted-merge button ────────────────────────
def test_review_keyboard_has_targeted_merge_button():
    kb = mod._devtask_review_keyboard("T1")
    datas = [b["callback_data"] for row in kb for b in row]
    assert "devtask:mergetarget:T1" in datas


# ── callback routes mergetarget → targeted mode ─────────────────────────────
def test_mergetarget_callback_runs_targeted_mode(monkeypatch, tmp_path):
    q, tid = _seed_awaiting(monkeypatch, tmp_path)
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

    mod.handle_callback_query({
        "id": "cq1", "from": {"id": int(ADMIN)},
        "message": {"chat": {"id": int(ADMIN)}, "message_id": 1},
        "data": "devtask:mergetarget:%s" % tid,
    }, {})
    assert got["tid"] == tid
    assert got["kwargs"].get("mode") == "targeted"


# ── targeted merge runs the targeted gate, not the full regress ─────────────
def test_targeted_merge_uses_targeted_gate_not_full_regress(monkeypatch, tmp_path):
    q, tid = _seed_awaiting(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    full_called = {"x": False}
    monkeypatch.setattr(mod, "_devtask_run_regress",
                        lambda *a, **k: full_called.update(x=True) or {"ok": True, "text": ""})
    seen = {}
    monkeypatch.setattr(mod, "_devtask_run_targeted",
                        lambda wt, base: seen.update(wt=wt, base=base) or
                        {"ok": True, "text": "🎯 3 passed", "mode": "targeted"})
    from app.services.devtask import git_ops as g, boot_watch as bw
    monkeypatch.setattr(g, "is_merged", lambda *a, **k: False)
    monkeypatch.setattr(g, "is_ff_clean", lambda *a, **k: True)
    monkeypatch.setattr(g, "prod_head", lambda *a, **k: "n")
    monkeypatch.setattr(g, "ff_merge", lambda *a, **k: None)
    monkeypatch.setattr(bw, "write_pending_restart", lambda *a, **k: None)
    monkeypatch.setattr(bw, "write_boot_watch", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_devtask_restart", lambda cid: None)

    mod._devtask_merge(ADMIN, tid, mode="targeted")
    assert full_called["x"] is False                 # full regress NOT run
    assert seen["wt"] == "C:/wt/devtask-X" and seen["base"] == "base1"
    assert q.get(tid)["status"] == STATUS_MERGED


# ── mode is recorded on the merged task (report) ────────────────────────────
def test_merge_records_mode_on_task(monkeypatch, tmp_path):
    q, tid = _seed_awaiting(monkeypatch, tmp_path)
    msgs = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: msgs.append(t))
    monkeypatch.setattr(mod, "_devtask_run_targeted",
                        lambda wt, base: {"ok": True, "text": "🎯 3 passed", "mode": "targeted"})
    from app.services.devtask import git_ops as g, boot_watch as bw
    monkeypatch.setattr(g, "is_merged", lambda *a, **k: False)
    monkeypatch.setattr(g, "is_ff_clean", lambda *a, **k: True)
    monkeypatch.setattr(g, "prod_head", lambda *a, **k: "n")
    monkeypatch.setattr(g, "ff_merge", lambda *a, **k: None)
    monkeypatch.setattr(bw, "write_pending_restart", lambda *a, **k: None)
    monkeypatch.setattr(bw, "write_boot_watch", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_devtask_restart", lambda cid: None)

    mod._devtask_merge(ADMIN, tid, mode="targeted")
    assert q.get(tid)["regress_mode"] == "targeted"


def test_full_merge_records_full_mode(monkeypatch, tmp_path):
    q, tid = _seed_awaiting(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_devtask_run_regress", lambda *a, **k: {"ok": True, "text": "129==129"})
    from app.services.devtask import git_ops as g, boot_watch as bw
    monkeypatch.setattr(g, "is_merged", lambda *a, **k: False)
    monkeypatch.setattr(g, "is_ff_clean", lambda *a, **k: True)
    monkeypatch.setattr(g, "prod_head", lambda *a, **k: "n")
    monkeypatch.setattr(g, "ff_merge", lambda *a, **k: None)
    monkeypatch.setattr(bw, "write_pending_restart", lambda *a, **k: None)
    monkeypatch.setattr(bw, "write_boot_watch", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_devtask_restart", lambda cid: None)

    mod._devtask_merge(ADMIN, tid, mode="full")
    assert q.get(tid)["regress_mode"] == "full"


# ── targeted gate: empty target set must NOT auto-pass (honest failure) ──────
def test_targeted_empty_targets_blocks_merge(monkeypatch, tmp_path):
    q, tid = _seed_awaiting(monkeypatch, tmp_path)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    monkeypatch.setattr(mod, "_devtask_run_targeted",
                        lambda wt, base: {"ok": False, "mode": "targeted",
                                          "text": "🎯 не найдено тестов под дифф"})
    merged = {"x": False}
    monkeypatch.setattr(mod, "_devtask_do_merge", lambda *a, **k: merged.update(x=True))
    from app.services.devtask import git_ops as g
    monkeypatch.setattr(g, "is_merged", lambda *a, **k: False)
    mod._devtask_merge(ADMIN, tid, mode="targeted")
    assert merged["x"] is False
    assert q.get(tid)["status"] == STATUS_AWAITING_REVIEW
    assert any("заблокирован" in s for s in sent)


# ── _devtask_run_targeted: real function, mocked git + pytest ───────────────
def test_run_targeted_no_targets_returns_not_ok(monkeypatch):
    from app.services.devtask import target_tests as tt
    monkeypatch.setattr(tt, "changed_paths", lambda *a, **k: ["docs/x.md"])
    monkeypatch.setattr(tt, "list_test_files", lambda *a, **k: ["tests/test_queue.py"])
    res = mod._devtask_run_targeted("C:/wt", "base1")
    assert res["ok"] is False and res["mode"] == "targeted"


def test_run_targeted_runs_only_mapped_tests(monkeypatch):
    from app.services.devtask import target_tests as tt
    monkeypatch.setattr(tt, "changed_paths",
                        lambda *a, **k: ["app/services/devtask/queue.py"])
    monkeypatch.setattr(tt, "list_test_files",
                        lambda *a, **k: ["tests/test_queue.py", "tests/test_runner.py"])
    captured = {}

    class _P:
        stdout = "3 passed in 0.1s"
        returncode = 0

    def fake_run(argv, **kw):
        captured["argv"] = argv
        return _P()

    import subprocess as _sp; monkeypatch.setattr(_sp, "run", fake_run)
    res = mod._devtask_run_targeted("C:/wt", "base1")
    assert res["ok"] is True and res["mode"] == "targeted"
    assert "tests/test_queue.py" in captured["argv"]
    assert "tests/test_runner.py" not in captured["argv"]  # only the mapped test


def test_run_targeted_fails_when_targeted_tests_fail(monkeypatch):
    from app.services.devtask import target_tests as tt
    monkeypatch.setattr(tt, "changed_paths",
                        lambda *a, **k: ["app/services/devtask/queue.py"])
    monkeypatch.setattr(tt, "list_test_files", lambda *a, **k: ["tests/test_queue.py"])

    class _P:
        stdout = "1 failed, 2 passed in 0.1s"
        returncode = 1

    import subprocess as _sp; monkeypatch.setattr(_sp, "run", lambda *a, **k: _P())
    res = mod._devtask_run_targeted("C:/wt", "base1")
    assert res["ok"] is False and res["mode"] == "targeted"
