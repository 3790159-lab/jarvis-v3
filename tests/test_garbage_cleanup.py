# -*- coding: utf-8 -*-
"""Tests for :mod:`app.services.garbage_cleanup` — weekly garbage-cleanup classifier.

Pure classifiers (no I/O) get boundary-condition tests in both directions.
Scanners run against tmp_path only — never touches real state/artifacts/logs
or real worktrees. Nothing here calls a paid API or mutates prod.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.services.garbage_cleanup import (
    INCOMING_FILES_MAX_AGE_DAYS,
    LOG_ROTATION_MAX_AGE_DAYS,
    WORKTREE_MIN_AGE_DAYS,
    TMP_ARTIFACT_MIN_AGE_DAYS,
    CATEGORY_INCOMING,
    CATEGORY_TMP_ARTIFACT,
    CATEGORY_LOG_ROTATION,
    CATEGORY_WORKTREE,
    is_incoming_file_stale,
    is_tmp_artifact_name,
    is_rotated_log_name,
    is_log_rotation_stale,
    is_worktree_remnant_deletable,
    scan_incoming_files,
    scan_tmp_artifacts,
    scan_log_rotation,
    scan_worktree_remnants,
    build_report,
    format_report_message,
    delete_candidates,
    run_cleanup,
)

NOW = datetime(2026, 7, 13, 12, 0, 0)


def _touch(path: Path, age_days: float, is_dir: bool = False, content: bytes = b"x") -> Path:
    if is_dir:
        path.mkdir(parents=True, exist_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    mtime = (NOW - timedelta(days=age_days)).timestamp()
    os.utime(path, (mtime, mtime))
    return path


# ─── is_incoming_file_stale ──────────────────────────────────────────────────

class TestIncomingFileStale:
    def test_exactly_at_boundary_not_stale(self):
        mtime = NOW - timedelta(days=INCOMING_FILES_MAX_AGE_DAYS)
        assert is_incoming_file_stale(mtime, NOW) is False

    def test_one_second_past_boundary_stale(self):
        mtime = NOW - timedelta(days=INCOMING_FILES_MAX_AGE_DAYS, seconds=1)
        assert is_incoming_file_stale(mtime, NOW) is True

    def test_fresh_file_not_stale(self):
        mtime = NOW - timedelta(days=1)
        assert is_incoming_file_stale(mtime, NOW) is False

    def test_custom_max_age(self):
        mtime = NOW - timedelta(days=5)
        assert is_incoming_file_stale(mtime, NOW, max_age_days=3) is True
        assert is_incoming_file_stale(mtime, NOW, max_age_days=10) is False


# ─── is_tmp_artifact_name ────────────────────────────────────────────────────

class TestTmpArtifactName:
    def test_matches_tmp_suffix(self):
        assert is_tmp_artifact_name("render_batch_42_tmp") is True

    def test_matches_tmp_suffix_dir(self):
        assert is_tmp_artifact_name("upload_tmp") is True

    def test_rejects_non_tmp(self):
        assert is_tmp_artifact_name("render_batch_42") is False

    def test_rejects_tmp_in_middle(self):
        assert is_tmp_artifact_name("tmp_render_42") is False

    def test_rejects_tmp_substring_not_suffix(self):
        assert is_tmp_artifact_name("render_temp") is False


# ─── is_rotated_log_name / is_log_rotation_stale ─────────────────────────────

class TestRotatedLogName:
    def test_numeric_rotation_suffix(self):
        assert is_rotated_log_name("jarvis_bot.log.1") is True

    def test_date_rotation_suffix(self):
        assert is_rotated_log_name("jarvis_bot.log.2026-01-01") is True

    def test_gz_rotation_suffix(self):
        assert is_rotated_log_name("jarvis_bot.log.gz") is True

    def test_active_log_is_not_rotated(self):
        assert is_rotated_log_name("jarvis_bot.log") is False

    def test_unrelated_file_is_not_rotated(self):
        assert is_rotated_log_name("readme.md") is False


class TestLogRotationStale:
    def test_active_log_never_stale_even_if_old(self):
        mtime = NOW - timedelta(days=999)
        assert is_log_rotation_stale("jarvis_bot.log", mtime, NOW) is False

    def test_rotated_log_stale_past_boundary(self):
        mtime = NOW - timedelta(days=LOG_ROTATION_MAX_AGE_DAYS + 1)
        assert is_log_rotation_stale("jarvis_bot.log.1", mtime, NOW) is True

    def test_rotated_log_not_stale_within_boundary(self):
        mtime = NOW - timedelta(days=LOG_ROTATION_MAX_AGE_DAYS - 1)
        assert is_log_rotation_stale("jarvis_bot.log.1", mtime, NOW) is False


# ─── is_worktree_remnant_deletable ───────────────────────────────────────────

class TestWorktreeRemnantDeletable:
    @pytest.mark.parametrize("status", ["merged", "rolled_back"])
    def test_deletable_statuses_past_age(self, status):
        mtime = NOW - timedelta(days=WORKTREE_MIN_AGE_DAYS + 1)
        assert is_worktree_remnant_deletable(status, mtime, NOW) is True

    @pytest.mark.parametrize("status", ["merged", "rolled_back"])
    def test_deletable_statuses_too_fresh(self, status):
        mtime = NOW - timedelta(days=WORKTREE_MIN_AGE_DAYS - 1)
        assert is_worktree_remnant_deletable(status, mtime, NOW) is False

    @pytest.mark.parametrize("status", ["running", "awaiting_review", "queued", "failed", None, "bogus"])
    def test_non_deletable_statuses_never_deleted(self, status):
        mtime = NOW - timedelta(days=999)
        assert is_worktree_remnant_deletable(status, mtime, NOW) is False

    def test_exactly_at_age_boundary_is_deletable(self):
        mtime = NOW - timedelta(days=WORKTREE_MIN_AGE_DAYS)
        assert is_worktree_remnant_deletable("merged", mtime, NOW) is True


# ─── scan_incoming_files ──────────────────────────────────────────────────────

class TestScanIncomingFiles:
    def test_missing_dir_returns_empty(self, tmp_path):
        assert scan_incoming_files(tmp_path / "nope", NOW) == []

    def test_stale_file_included(self, tmp_path):
        _touch(tmp_path / "old.jpg", INCOMING_FILES_MAX_AGE_DAYS + 1)
        out = scan_incoming_files(tmp_path, NOW)
        assert len(out) == 1
        assert out[0]["category"] == CATEGORY_INCOMING

    def test_fresh_file_excluded(self, tmp_path):
        _touch(tmp_path / "new.jpg", 1)
        assert scan_incoming_files(tmp_path, NOW) == []

    def test_size_bytes_reported(self, tmp_path):
        _touch(tmp_path / "old.jpg", 20, content=b"x" * 1000)
        out = scan_incoming_files(tmp_path, NOW)
        assert out[0]["size_bytes"] == 1000


# ─── scan_tmp_artifacts ───────────────────────────────────────────────────────

class TestScanTmpArtifacts:
    def test_missing_dir_returns_empty(self, tmp_path):
        assert scan_tmp_artifacts(tmp_path / "nope", NOW) == []

    def test_tmp_dir_included_when_old_enough(self, tmp_path):
        d = tmp_path / "batch42_tmp"
        d.mkdir()
        (d / "f.bin").write_bytes(b"y" * 500)
        _touch(d, TMP_ARTIFACT_MIN_AGE_DAYS + 1, is_dir=True)  # set mtime AFTER writing inner file
        out = scan_tmp_artifacts(tmp_path, NOW)
        assert len(out) == 1
        assert out[0]["category"] == CATEGORY_TMP_ARTIFACT
        assert out[0]["size_bytes"] == 500

    def test_non_tmp_dir_excluded(self, tmp_path):
        _touch(tmp_path / "batch42", TMP_ARTIFACT_MIN_AGE_DAYS + 1, is_dir=True)
        assert scan_tmp_artifacts(tmp_path, NOW) == []

    def test_too_fresh_tmp_dir_excluded_to_avoid_mid_write_race(self, tmp_path):
        _touch(tmp_path / "batch42_tmp", 0, is_dir=True)
        assert scan_tmp_artifacts(tmp_path, NOW) == []


# ─── scan_log_rotation ────────────────────────────────────────────────────────

class TestScanLogRotation:
    def test_missing_dir_returns_empty(self, tmp_path):
        assert scan_log_rotation(tmp_path / "nope", NOW) == []

    def test_stale_rotated_log_included(self, tmp_path):
        _touch(tmp_path / "jarvis_bot.log.1", LOG_ROTATION_MAX_AGE_DAYS + 1)
        out = scan_log_rotation(tmp_path, NOW)
        assert len(out) == 1
        assert out[0]["category"] == CATEGORY_LOG_ROTATION

    def test_active_log_never_included(self, tmp_path):
        _touch(tmp_path / "jarvis_bot.log", LOG_ROTATION_MAX_AGE_DAYS + 100)
        assert scan_log_rotation(tmp_path, NOW) == []

    def test_fresh_rotated_log_excluded(self, tmp_path):
        _touch(tmp_path / "jarvis_bot.log.1", 5)
        assert scan_log_rotation(tmp_path, NOW) == []


# ─── scan_worktree_remnants ───────────────────────────────────────────────────

class TestScanWorktreeRemnants:
    def test_missing_dir_returns_empty(self, tmp_path):
        assert scan_worktree_remnants(tmp_path / "nope", lambda tid: "merged", NOW) == []

    def test_merged_old_worktree_included(self, tmp_path):
        _touch(tmp_path / "devtask-abc123", WORKTREE_MIN_AGE_DAYS + 1, is_dir=True)
        out = scan_worktree_remnants(tmp_path, lambda tid: "merged", NOW)
        assert len(out) == 1
        assert out[0]["task_id"] == "abc123"
        assert out[0]["category"] == CATEGORY_WORKTREE

    def test_running_worktree_never_included(self, tmp_path):
        _touch(tmp_path / "devtask-running1", WORKTREE_MIN_AGE_DAYS + 100, is_dir=True)
        assert scan_worktree_remnants(tmp_path, lambda tid: "running", NOW) == []

    def test_unknown_status_never_included(self, tmp_path):
        _touch(tmp_path / "devtask-ghost1", WORKTREE_MIN_AGE_DAYS + 100, is_dir=True)
        assert scan_worktree_remnants(tmp_path, lambda tid: None, NOW) == []

    def test_self_worktree_never_included_even_if_merged(self, tmp_path):
        wt = _touch(tmp_path / "devtask-selfid", WORKTREE_MIN_AGE_DAYS + 1, is_dir=True)
        out = scan_worktree_remnants(
            tmp_path, lambda tid: "merged", NOW, self_worktree=str(wt),
        )
        assert out == []

    def test_non_devtask_dirs_ignored(self, tmp_path):
        _touch(tmp_path / "some_other_dir", WORKTREE_MIN_AGE_DAYS + 100, is_dir=True)
        assert scan_worktree_remnants(tmp_path, lambda tid: "merged", NOW) == []


# ─── build_report / format_report_message ────────────────────────────────────

class TestBuildReport:
    def test_empty_candidates(self):
        report = build_report([])
        assert report["total_count"] == 0
        assert report["total_mb"] == 0
        assert report["categories"] == {}

    def test_aggregates_by_category(self):
        candidates = [
            {"category": CATEGORY_INCOMING, "size_bytes": 1024 * 1024},
            {"category": CATEGORY_INCOMING, "size_bytes": 1024 * 1024},
            {"category": CATEGORY_WORKTREE, "size_bytes": 2 * 1024 * 1024},
        ]
        report = build_report(candidates)
        assert report["categories"][CATEGORY_INCOMING]["count"] == 2
        assert report["categories"][CATEGORY_INCOMING]["size_mb"] == 2.0
        assert report["categories"][CATEGORY_WORKTREE]["count"] == 1
        assert report["total_count"] == 3
        assert report["total_mb"] == 4.0


class TestFormatReportMessage:
    def test_dry_run_header(self):
        report = build_report([])
        msg = format_report_message(report, dry_run=True)
        assert "DRY-RUN" in msg

    def test_real_run_header_no_dry_run_word(self):
        report = build_report([])
        msg = format_report_message(report, dry_run=False)
        assert "DRY-RUN" not in msg

    def test_empty_report_says_clean(self):
        report = build_report([])
        msg = format_report_message(report, dry_run=True)
        assert "чист" in msg.lower() or "не найдено" in msg.lower()

    def test_reports_total_mb(self):
        candidates = [{"category": CATEGORY_INCOMING, "size_bytes": 5 * 1024 * 1024}]
        report = build_report(candidates)
        msg = format_report_message(report, dry_run=True)
        assert "5.0" in msg or "5.00" in msg


# ─── delete_candidates ────────────────────────────────────────────────────────

class TestDeleteCandidates:
    def test_deletes_file(self, tmp_path):
        f = _touch(tmp_path / "old.jpg", 20)
        results = delete_candidates([{"path": str(f), "category": CATEGORY_INCOMING, "size_bytes": 1}])
        assert results[0]["deleted"] is True
        assert not f.exists()

    def test_deletes_dir(self, tmp_path):
        d = _touch(tmp_path / "batch_tmp", 20, is_dir=True)
        (d / "f.bin").write_bytes(b"z")
        results = delete_candidates([{"path": str(d), "category": CATEGORY_TMP_ARTIFACT, "size_bytes": 1}])
        assert results[0]["deleted"] is True
        assert not d.exists()

    def test_worktree_remnant_uses_injected_remover(self, tmp_path):
        d = _touch(tmp_path / "devtask-xyz", 20, is_dir=True)
        removed_ids = []
        results = delete_candidates(
            [{"path": str(d), "category": CATEGORY_WORKTREE, "size_bytes": 1, "task_id": "xyz"}],
            remove_worktree_fn=lambda tid: removed_ids.append(tid),
        )
        assert removed_ids == ["xyz"]
        assert results[0]["deleted"] is True

    def test_error_captured_not_raised(self, tmp_path):
        missing = tmp_path / "does_not_exist.jpg"
        results = delete_candidates([{"path": str(missing), "category": CATEGORY_INCOMING, "size_bytes": 1}])
        # Missing path: nothing to delete, but must not raise.
        assert "error" not in results[0] or results[0]["deleted"] is False


# ─── run_cleanup (orchestrator) ───────────────────────────────────────────────

class TestRunCleanup:
    def _dirs(self, tmp_path):
        return {
            "incoming_dir": tmp_path / "incoming",
            "artifacts_dir": tmp_path / "artifacts",
            "logs_dir": tmp_path / "logs",
            "wt_root": tmp_path / "worktrees",
        }

    def test_dry_run_never_deletes(self, tmp_path):
        dirs = self._dirs(tmp_path)
        f = _touch(dirs["incoming_dir"] / "old.jpg", INCOMING_FILES_MAX_AGE_DAYS + 1)
        report = run_cleanup(
            **dirs, get_status=lambda tid: None, now=NOW, dry_run=True,
        )
        assert f.exists()
        assert report["dry_run"] is True
        assert report["total_count"] == 1

    def test_real_run_deletes(self, tmp_path):
        dirs = self._dirs(tmp_path)
        f = _touch(dirs["incoming_dir"] / "old.jpg", INCOMING_FILES_MAX_AGE_DAYS + 1)
        report = run_cleanup(
            **dirs, get_status=lambda tid: None, now=NOW, dry_run=False,
        )
        assert not f.exists()
        assert report["dry_run"] is False

    def test_worktree_removal_goes_through_injected_fn(self, tmp_path):
        dirs = self._dirs(tmp_path)
        _touch(dirs["wt_root"] / "devtask-abc", WORKTREE_MIN_AGE_DAYS + 1, is_dir=True)
        removed = []
        report = run_cleanup(
            **dirs, get_status=lambda tid: "merged", now=NOW, dry_run=False,
            remove_worktree_fn=lambda tid: removed.append(tid),
        )
        assert removed == ["abc"]
        assert report["total_count"] == 1

    def test_no_garbage_found_is_clean_report(self, tmp_path):
        dirs = self._dirs(tmp_path)
        report = run_cleanup(**dirs, get_status=lambda tid: None, now=NOW, dry_run=True)
        assert report["total_count"] == 0
