# -*- coding: utf-8 -*-
"""DEV-11 detached launcher (scripts/devtask_cc_launcher.py). $0, mocks only —
the real CC (``runner.run``) is NEVER invoked here, same discipline as
tests/test_devtask_runner.py.

Loaded via importlib (mirrors test_regress_watch_check.py) since it's a
scripts/ entry point, not a package module.
"""
import importlib.util as _ilu
from pathlib import Path as _P

_spec = _ilu.spec_from_file_location(
    "devtask_cc_launcher",
    _P(__file__).resolve().parent.parent / "scripts" / "devtask_cc_launcher.py")
launcher = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(launcher)

from app.services.devtask.queue import DevTaskQueue


def test_main_requires_task_id_arg():
    assert launcher.main(["devtask_cc_launcher.py"]) == 2


def test_main_writes_failed_result_when_task_missing(tmp_path):
    q = DevTaskQueue(base_dir=tmp_path)
    rc = launcher.main(["x", "no-such-task"], queue=q)
    assert rc == 1
    res = launcher._r.read_cc_result(str(tmp_path / "no-such-task" / "cc_result.json"))
    assert res["status"] == "failed"
    assert "no worktree" in res["reason"]


def test_main_writes_failed_result_when_no_worktree_recorded(tmp_path, monkeypatch):
    q = DevTaskQueue(base_dir=tmp_path)
    tid = q.add("do X")
    monkeypatch.setattr(launcher._r, "run",
                        lambda **k: (_ for _ in ()).throw(AssertionError("CC must not run")))
    rc = launcher.main(["x", tid], queue=q)
    assert rc == 1
    res = launcher._r.read_cc_result(str(tmp_path / tid / "cc_result.json"))
    assert res["status"] == "failed"


def test_main_runs_cc_and_persists_awaiting_review_result(tmp_path, monkeypatch):
    q = DevTaskQueue(base_dir=tmp_path)
    tid = q.add("build X")
    wt = tmp_path / "wt"
    wt.mkdir()
    q.set_status(tid, "running", worktree=str(wt), session_id="sid-1")
    seen = {}

    def fake_run(**kw):
        seen.update(kw)
        return {"status": "awaiting_review", "session_id": "sid-1", "cost": 0.5}

    monkeypatch.setattr(launcher._r, "run", fake_run)
    rc = launcher.main(["x", tid], queue=q)
    assert rc == 0
    # CC ran with the persisted worktree as cwd and a report path UNDER it
    assert seen["cwd"] == str(wt)
    rp = seen["report_path"].replace("\\", "/")
    assert rp.startswith(str(wt).replace("\\", "/"))
    assert rp.endswith("state/dev_tasks/%s/report.md" % tid)
    res = launcher._r.read_cc_result(str(tmp_path / tid / "cc_result.json"))
    assert res == {"status": "awaiting_review", "session_id": "sid-1", "cost": 0.5}


def test_main_persists_report_to_live_tree_on_awaiting_review(tmp_path, monkeypatch):
    # DEV-96: report.md lives only inside the worktree while CC writes it; once
    # the worktree is later removed (rollback/merge cleanup) the report must
    # already have a copy in the live tree's state/dev_tasks/<id>/ dir.
    q = DevTaskQueue(base_dir=tmp_path)
    tid = q.add("build X")
    wt = tmp_path / "wt"
    wt.mkdir()
    report_dir = wt / "state" / "dev_tasks" / tid
    report_dir.mkdir(parents=True)
    (report_dir / "report.md").write_text("VERDICT: READY", encoding="utf-8")
    q.set_status(tid, "running", worktree=str(wt), session_id="sid-1")

    def fake_run(**kw):
        return {"status": "awaiting_review", "session_id": "sid-1", "cost": 0.5,
                "report_present": True}

    monkeypatch.setattr(launcher._r, "run", fake_run)
    rc = launcher.main(["x", tid], queue=q)
    assert rc == 0
    live_report = tmp_path / tid / "report.md"
    assert live_report.read_text(encoding="utf-8") == "VERDICT: READY"


def test_main_skips_report_copy_when_not_present(tmp_path, monkeypatch):
    # A stale report.md can genuinely sit in the worktree (leftover from a
    # prior run, or CC wrote a partial file then crashed) even when THIS run's
    # result says report_present is falsy — the copy must key off report_present,
    # not off the file merely existing on disk.
    q = DevTaskQueue(base_dir=tmp_path)
    tid = q.add("build X")
    wt = tmp_path / "wt"
    report_dir = wt / "state" / "dev_tasks" / tid
    report_dir.mkdir(parents=True)
    (report_dir / "report.md").write_text("stale leftover", encoding="utf-8")
    q.set_status(tid, "running", worktree=str(wt), session_id="sid-1")

    def fake_run(**kw):
        return {"status": "failed", "reason": "no_report", "report_present": False}

    monkeypatch.setattr(launcher._r, "run", fake_run)
    rc = launcher.main(["x", tid], queue=q)
    assert rc == 0
    assert not (tmp_path / tid / "report.md").exists()


def test_main_persists_failed_result_on_run_exception(tmp_path, monkeypatch):
    # The launcher must never crash silently — an exception mid-run() still
    # yields an honest cc_result.json (honest failure > lying success).
    q = DevTaskQueue(base_dir=tmp_path)
    tid = q.add("build X")
    wt = tmp_path / "wt"
    wt.mkdir()
    q.set_status(tid, "running", worktree=str(wt), session_id="sid-1")
    monkeypatch.setattr(launcher._r, "run",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("boom")))
    rc = launcher.main(["x", tid], queue=q)
    assert rc == 0
    res = launcher._r.read_cc_result(str(tmp_path / tid / "cc_result.json"))
    assert res["status"] == "failed"
    assert "boom" in res["reason"]
