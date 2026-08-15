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


# ── docs-only classifier: *.md, docs/, research/, artifacts/** ───────────────
def test_docs_only_diff_true_for_markdown_files():
    assert tt.is_docs_only_diff(["README.md", "docs/MASTER-PLAN.md"]) is True


def test_docs_only_diff_true_for_research_and_artifacts_dirs():
    # artifacts/** counts as docs-only regardless of the file's own extension
    # (e.g. archived .py patch backups under artifacts/patch_backups/).
    changed = ["research/notes.md",
               "artifacts/patch_backups/jarvis_brain_v2/main_20260429_130339.py"]
    assert tt.is_docs_only_diff(changed) is True


def test_docs_only_diff_false_when_any_code_file_present():
    changed = ["docs/plan.md", "app/services/devtask/queue.py"]
    assert tt.is_docs_only_diff(changed) is False


def test_docs_only_diff_false_for_config_or_script_files():
    assert tt.is_docs_only_diff(["config/settings.yaml"]) is False


def test_docs_only_diff_false_when_empty():
    assert tt.is_docs_only_diff([]) is False


def test_docs_only_diff_normalizes_backslash_paths():
    assert tt.is_docs_only_diff(["docs\\MASTER-PLAN.md", "research\\notes.md"]) is True


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


# ── tests/ живёт в подпапках (tests/chatter/…) — гейт обязан их видеть ───────
# Инцидент 0f24fd (/allow, 495ec2e1): дифф добавил tests/chatter/test_allow_command.py
# — самый очевидный кандидат на прогон — а гейт сказал «не маппится ни на один
# тест», потому что видел только плоский tests/test_*.py.
def test_new_nested_test_file_in_diff_maps_to_itself():
    existing = ["tests/chatter/test_allow_command.py", "tests/test_queue.py"]
    changed = ["tests/chatter/test_allow_command.py"]
    assert tt.map_paths_to_tests(changed, existing) == ["tests/chatter/test_allow_command.py"]


def test_source_maps_to_nested_stem_test():
    existing = ["tests/chatter/test_console.py"]
    changed = ["chatter/core/console.py"]
    assert tt.map_paths_to_tests(changed, existing) == ["tests/chatter/test_console.py"]


def test_source_maps_to_every_matching_test_at_any_depth():
    # Одноимённые тесты в разных пакетах — берём ВСЕ (консервативно), а не первый.
    existing = ["tests/test_db.py", "tests/chatter/test_db.py"]
    changed = ["chatter/storage/db.py"]
    assert tt.map_paths_to_tests(changed, existing) == [
        "tests/chatter/test_db.py", "tests/test_db.py"]


def test_deleted_nested_test_file_not_selected():
    existing = ["tests/chatter/test_allow_command.py"]
    changed = ["tests/chatter/test_gone.py"]
    assert tt.map_paths_to_tests(changed, existing) == []


def test_list_test_files_walks_subdirectories(tmp_path):
    root = tmp_path / "tests"
    (root / "chatter").mkdir(parents=True)
    (root / "__pycache__").mkdir()
    (root / "test_queue.py").write_text("", encoding="utf-8")
    (root / "chatter" / "test_allow_command.py").write_text("", encoding="utf-8")
    (root / "chatter" / "conftest.py").write_text("", encoding="utf-8")
    (root / "__pycache__" / "test_stale.py").write_text("", encoding="utf-8")

    out = tt.list_test_files(str(tmp_path))
    assert out == ["tests/chatter/test_allow_command.py", "tests/test_queue.py"]


def test_list_test_files_empty_when_no_tests_dir(tmp_path):
    assert tt.list_test_files(str(tmp_path)) == []
