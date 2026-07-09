# -*- coding: utf-8 -*-
"""Targeted-test selection for the dev_task merge gate — pure mapping + git seam.

$0, mocks only. No CC, no network, no real pytest run here.
"""
from app.services.devtask import target_tests as tt


# ── pure mapping: changed repo paths → subset of existing test files ─────────
def test_source_file_maps_to_stem_test():
    existing = ["tests/test_queue.py", "tests/test_runner.py"]
    changed = ["app/services/devtask/queue.py"]
    assert tt.map_paths_to_tests(changed, existing) == ["tests/test_queue.py"]


def test_changed_test_file_included_directly():
    existing = ["tests/test_devtask_wiring.py", "tests/test_queue.py"]
    changed = ["tests/test_devtask_wiring.py"]
    assert tt.map_paths_to_tests(changed, existing) == ["tests/test_devtask_wiring.py"]


def test_source_without_matching_test_is_excluded():
    existing = ["tests/test_queue.py"]
    changed = ["app/services/devtask/runner.py"]  # no tests/test_runner.py present
    assert tt.map_paths_to_tests(changed, existing) == []


def test_deleted_test_file_not_selected():
    # A test file present in the diff but no longer on disk can't be run.
    existing = ["tests/test_queue.py"]
    changed = ["tests/test_gone.py"]
    assert tt.map_paths_to_tests(changed, existing) == []


def test_backslash_paths_are_normalized():
    existing = ["tests/test_queue.py"]
    changed = ["app\\services\\devtask\\queue.py"]
    assert tt.map_paths_to_tests(changed, existing) == ["tests/test_queue.py"]


def test_result_is_sorted_and_deduped():
    existing = ["tests/test_a.py", "tests/test_b.py"]
    # b's source and its own test both point at test_b.py → dedup; unordered in.
    changed = ["app/b.py", "tests/test_b.py", "app/a.py"]
    assert tt.map_paths_to_tests(changed, existing) == ["tests/test_a.py", "tests/test_b.py"]


def test_non_python_changes_map_to_nothing():
    existing = ["tests/test_queue.py"]
    changed = ["docs/MASTER-PLAN.md", "README.md"]
    assert tt.map_paths_to_tests(changed, existing) == []


# ── git seam: `git diff --name-only <base_head>` in the worktree ─────────────
def test_changed_paths_calls_git_diff_and_splits_lines():
    calls = {}

    class _R:
        returncode = 0
        stdout = "app/services/devtask/queue.py\ntests/test_queue.py\n"
        stderr = ""

    def fake_run(argv, **kw):
        calls["argv"] = argv
        calls["cwd"] = kw.get("cwd")
        return _R()

    out = tt.changed_paths("C:/wt/devtask-X", "base1", run=fake_run)
    assert out == ["app/services/devtask/queue.py", "tests/test_queue.py"]
    # diff of the branch's work against the task's base commit, run in the worktree
    assert calls["argv"][:3] == ["git", "-C", "C:/wt/devtask-X"]
    assert "diff" in calls["argv"] and "--name-only" in calls["argv"]
    assert "base1" in calls["argv"]


def test_changed_paths_empty_on_git_failure():
    class _R:
        returncode = 1
        stdout = ""
        stderr = "fatal"

    out = tt.changed_paths("C:/wt", "base1", run=lambda *a, **k: _R())
    assert out == []
