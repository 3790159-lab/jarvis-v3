# -*- coding: utf-8 -*-
"""DEV-13: merge-gate observability — targeted pytest run must be investigable.

Before: --tb=no + a single summary-count line meant a red gate gave no way to
tell WHICH test failed (real incident: DEV-1 failed 1/8, cause never found).
After: stdout is saved to state/dev_tasks/<id>_gate.log and failed node-ids
(with a short one-line reason from pytest's -rf summary) surface in the TG
block message and in [Details]. $0, mocks only, no real pytest/git spawn.
"""
import importlib

from app.services.devtask.queue import DevTaskQueue, STATUS_AWAITING_REVIEW, STATUS_MERGED

mod = importlib.import_module("tools.jarvis_smart_telegram_control")
ADMIN = "237616472"


def _seed_awaiting(monkeypatch, tmp_path, worktree="C:/wt/devtask-X", base="base1"):
    q = DevTaskQueue(base_dir=tmp_path)
    tid = q.add("do X")
    q.set_status(tid, STATUS_AWAITING_REVIEW, worktree=worktree, base_head=base,
                 branch=f"devtask-{tid}", report_path=str(tmp_path / tid / "report.md"))
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    return q, tid


_FAIL_STDOUT = (
    "F.F...\n"
    "=========================== short test summary info ===========================\n"
    "FAILED tests/test_queue.py::test_claim_twice - AssertionError: expected True\n"
    "FAILED tests/test_queue.py::test_add_dup - ValueError: dup id\n"
    "2 failed, 4 passed in 0.31s\n"
)


# ── pytest is invoked with -rf so failed node-ids + short reason are captured ──
def test_run_targeted_argv_includes_rf_flag(monkeypatch):
    from app.services.devtask import target_tests as tt
    monkeypatch.setattr(tt, "changed_paths", lambda *a, **k: ["app/services/devtask/queue.py"])
    monkeypatch.setattr(tt, "list_test_files", lambda *a, **k: ["tests/test_queue.py"])
    captured = {}

    class _P:
        stdout = "3 passed in 0.1s"
        returncode = 0

    def fake_run(argv, **kw):
        captured["argv"] = argv
        return _P()

    monkeypatch.setattr(mod._regress_watch, "run_guarded", fake_run)
    mod._devtask_run_targeted("C:/wt", "base1")
    assert "-rf" in captured["argv"]


# ── --tb=no dropped in favour of --tb=short so the gate log has a real traceback ──
def test_run_targeted_argv_uses_tb_short_not_no(monkeypatch):
    from app.services.devtask import target_tests as tt
    monkeypatch.setattr(tt, "changed_paths", lambda *a, **k: ["app/services/devtask/queue.py"])
    monkeypatch.setattr(tt, "list_test_files", lambda *a, **k: ["tests/test_queue.py"])
    captured = {}

    class _P:
        stdout = "3 passed in 0.1s"
        returncode = 0

    def fake_run(argv, **kw):
        captured["argv"] = argv
        return _P()

    monkeypatch.setattr(mod._regress_watch, "run_guarded", fake_run)
    mod._devtask_run_targeted("C:/wt", "base1")
    assert "--tb=short" in captured["argv"]
    assert "--tb=no" not in captured["argv"]


# ── gate log: stdout persisted to state/dev_tasks/<id>_gate.log ─────────────
def test_run_targeted_writes_gate_log(monkeypatch, tmp_path):
    from app.services.devtask import target_tests as tt
    q = DevTaskQueue(base_dir=tmp_path)
    tid = q.add("do X")
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    monkeypatch.setattr(tt, "changed_paths", lambda *a, **k: ["app/services/devtask/queue.py"])
    monkeypatch.setattr(tt, "list_test_files", lambda *a, **k: ["tests/test_queue.py"])

    class _P:
        stdout = _FAIL_STDOUT
        returncode = 1

    monkeypatch.setattr(mod._regress_watch, "run_guarded", lambda *a, **k: _P())
    res = mod._devtask_run_targeted("C:/wt", "base1", tid=tid)

    log_path = tmp_path / ("%s_gate.log" % tid)
    assert log_path.exists()
    assert log_path.read_text(encoding="utf-8") == _FAIL_STDOUT
    assert res["log_path"] == str(log_path)


# ── failed node-ids extracted from the -rf summary, exposed on the verdict ──
def test_run_targeted_extracts_failed_node_ids(monkeypatch, tmp_path):
    from app.services.devtask import target_tests as tt
    q = DevTaskQueue(base_dir=tmp_path)
    tid = q.add("do X")
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    monkeypatch.setattr(tt, "changed_paths", lambda *a, **k: ["app/services/devtask/queue.py"])
    monkeypatch.setattr(tt, "list_test_files", lambda *a, **k: ["tests/test_queue.py"])

    class _P:
        stdout = _FAIL_STDOUT
        returncode = 1

    monkeypatch.setattr(mod._regress_watch, "run_guarded", lambda *a, **k: _P())
    res = mod._devtask_run_targeted("C:/wt", "base1", tid=tid)

    assert res["ok"] is False
    assert res["failed_tests"] == [
        "tests/test_queue.py::test_claim_twice - AssertionError: expected True",
        "tests/test_queue.py::test_add_dup - ValueError: dup id",
    ]
    assert "test_claim_twice" in res["text"]
    assert str(tmp_path / ("%s_gate.log" % tid)) in res["text"]


# ── more than 3 failures: message shows first 3 + "...ещё N" ────────────────
def test_run_targeted_truncates_failed_list_to_three(monkeypatch, tmp_path):
    from app.services.devtask import target_tests as tt
    q = DevTaskQueue(base_dir=tmp_path)
    tid = q.add("do X")
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    monkeypatch.setattr(tt, "changed_paths", lambda *a, **k: ["app/services/devtask/queue.py"])
    monkeypatch.setattr(tt, "list_test_files", lambda *a, **k: ["tests/test_queue.py"])
    stdout_text = (
        "FFFFF\n"
        "=========================== short test summary info ===========================\n"
        "FAILED tests/test_queue.py::test_a - AssertionError\n"
        "FAILED tests/test_queue.py::test_b - AssertionError\n"
        "FAILED tests/test_queue.py::test_c - AssertionError\n"
        "FAILED tests/test_queue.py::test_d - AssertionError\n"
        "FAILED tests/test_queue.py::test_e - AssertionError\n"
        "5 failed in 0.31s\n"
    )

    class _P:
        stdout = stdout_text
        returncode = 1

    monkeypatch.setattr(mod._regress_watch, "run_guarded", lambda *a, **k: _P())
    res = mod._devtask_run_targeted("C:/wt", "base1", tid=tid)

    assert len(res["failed_tests"]) == 5
    assert "test_a" in res["text"] and "test_b" in res["text"] and "test_c" in res["text"]
    assert "test_d" not in res["text"] and "test_e" not in res["text"]
    assert "...ещё 2" in res["text"]


# ── tid is optional (back-compat: no log written, no crash) ─────────────────
def test_run_targeted_without_tid_skips_log(monkeypatch):
    from app.services.devtask import target_tests as tt
    monkeypatch.setattr(tt, "changed_paths", lambda *a, **k: ["app/services/devtask/queue.py"])
    monkeypatch.setattr(tt, "list_test_files", lambda *a, **k: ["tests/test_queue.py"])

    class _P:
        stdout = _FAIL_STDOUT
        returncode = 1

    monkeypatch.setattr(mod._regress_watch, "run_guarded", lambda *a, **k: _P())
    res = mod._devtask_run_targeted("C:/wt", "base1")
    assert res["log_path"] is None
    assert res["failed_tests"]


# ── end-to-end: blocked merge message surfaces failed test names + log path ─
def test_devtask_merge_blocked_message_shows_failed_tests(monkeypatch, tmp_path):
    q, tid = _seed_awaiting(monkeypatch, tmp_path)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    log_path = str(tmp_path / ("%s_gate.log" % tid))
    monkeypatch.setattr(
        mod, "_devtask_run_targeted",
        lambda wt, base, tid=None: {
            "ok": False, "mode": "targeted",
            "text": "🎯 таргет-тесты по диффу (1 файлов): 🚫 1 failed, 2 passed, 0 errors\n"
                    "• tests/test_queue.py::test_claim_twice - AssertionError: expected True\n"
                    "лог: %s" % log_path,
            "failed_tests": ["tests/test_queue.py::test_claim_twice - AssertionError: expected True"],
            "log_path": log_path,
        })
    from app.services.devtask import git_ops as g
    monkeypatch.setattr(g, "is_merged", lambda *a, **k: False)

    mod._devtask_merge(ADMIN, tid, mode="targeted")

    assert any("test_claim_twice" in s for s in sent)
    assert any(log_path in s for s in sent)
    item = q.get(tid)
    assert item["gate_failed_tests"] == ["tests/test_queue.py::test_claim_twice - AssertionError: expected True"]
    assert item["gate_log"] == log_path
    assert item["status"] == STATUS_AWAITING_REVIEW  # not merged


# ── [Details] shows the failed tests + log path recorded on the card ────────
def test_devtask_details_shows_failed_tests(monkeypatch, tmp_path):
    q, tid = _seed_awaiting(monkeypatch, tmp_path)
    log_path = str(tmp_path / ("%s_gate.log" % tid))
    q.set_status(tid, STATUS_AWAITING_REVIEW,
                 gate_failed_tests=["tests/test_queue.py::test_claim_twice - AssertionError: expected True"],
                 gate_log=log_path)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))

    mod._devtask_details(ADMIN, tid)

    assert len(sent) == 1
    assert "test_claim_twice" in sent[0]
    assert log_path in sent[0]
