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
                        lambda wt, base, tid=None: seen.update(wt=wt, base=base) or
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
                        lambda wt, base, tid=None: {"ok": True, "text": "🎯 3 passed", "mode": "targeted"})
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
                        lambda wt, base, tid=None: {"ok": False, "mode": "targeted",
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
    # non-doc .py change with no name-derivable test → honest "can't verify" block
    monkeypatch.setattr(tt, "changed_paths", lambda *a, **k: ["app/services/devtask/runner.py"])
    monkeypatch.setattr(tt, "list_test_files", lambda *a, **k: ["tests/test_queue.py"])
    res = mod._devtask_run_targeted("C:/wt", "base1")
    assert res["ok"] is False and res["mode"] == "targeted"


# ── docs-only diff: skip merge gate instead of blocking "не маппится" ───────
def test_run_targeted_docs_only_diff_skips_with_message(monkeypatch):
    from app.services.devtask import target_tests as tt
    monkeypatch.setattr(tt, "changed_paths",
                        lambda *a, **k: ["docs/MASTER-PLAN.md", "README.md"])
    monkeypatch.setattr(tt, "list_test_files", lambda *a, **k: ["tests/test_queue.py"])
    res = mod._devtask_run_targeted("C:/wt", "base1")
    assert res["ok"] is True and res["mode"] == "docs_only"
    assert "📄" in res["text"] and "docs-only" in res["text"]


def test_docs_only_merge_sends_marker_message_and_proceeds(monkeypatch, tmp_path):
    # End-to-end through _devtask_merge: docs-only verdict must surface the
    # "📄 docs-only" marker to the human and still complete the FF-merge.
    q, tid = _seed_awaiting(monkeypatch, tmp_path)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    monkeypatch.setattr(mod, "_devtask_run_targeted",
                        lambda wt, base, tid=None: {"ok": True, "mode": "docs_only",
                                          "text": "📄 docs-only дифф (2 файлов) — тесты не требуются"})
    from app.services.devtask import git_ops as g, boot_watch as bw
    monkeypatch.setattr(g, "is_merged", lambda *a, **k: False)
    monkeypatch.setattr(g, "is_ff_clean", lambda *a, **k: True)
    monkeypatch.setattr(g, "prod_head", lambda *a, **k: "n")
    monkeypatch.setattr(g, "ff_merge", lambda *a, **k: None)
    monkeypatch.setattr(bw, "write_pending_restart", lambda *a, **k: None)
    monkeypatch.setattr(bw, "write_boot_watch", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_devtask_restart", lambda cid: None)

    mod._devtask_merge(ADMIN, tid, mode="targeted")
    assert any("📄" in s and "docs-only" in s for s in sent)
    assert q.get(tid)["status"] == STATUS_MERGED


def test_run_targeted_docs_plus_code_diff_is_not_docs_only(monkeypatch):
    # docs + py with no mapped test → still blocked as before (not docs-only)
    from app.services.devtask import target_tests as tt
    monkeypatch.setattr(tt, "changed_paths",
                        lambda *a, **k: ["docs/MASTER-PLAN.md", "app/services/devtask/runner.py"])
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

    # The spawn seam is now the detached-watchdog runner (Этап 1, хвост #6); the
    # pytest argv it receives is unchanged from the old subprocess.run.
    monkeypatch.setattr(mod._regress_watch, "run_guarded", fake_run)
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

    monkeypatch.setattr(mod._regress_watch, "run_guarded", lambda *a, **k: _P())
    res = mod._devtask_run_targeted("C:/wt", "base1")
    assert res["ok"] is False and res["mode"] == "targeted"
