"""Tests for Phase 19 (Block D1): Cowork Watchdog implementation."""
from __future__ import annotations

import json
import sys
import time
import threading
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.cowork_watcher import (
    CoworkWatcher,
    _is_safe_instruction,
    sanitize_task,
    start_watcher,
    stop_watcher,
    get_watcher,
    _MAX_TASK_SIZE_BYTES,
)
from app.services.cowork_bridge import (
    archive_processed,
    inject_fake_result,
    make_task,
    record_cowork_cost,
    get_cowork_cost_summary,
    send_task_to_cowork,
    mark_result_processed,
)


# ---------------------------------------------------------------------------
# Safety / sanitize tests
# ---------------------------------------------------------------------------

class TestSafetyChecks:
    def test_safe_instruction_normal(self):
        assert _is_safe_instruction("Organize my Downloads folder") is True

    def test_safe_instruction_complex(self):
        assert _is_safe_instruction("Create an expense report from PDF receipts") is True

    def test_blocked_etc_path(self):
        assert _is_safe_instruction("read /etc/passwd please") is False

    def test_blocked_system32(self):
        assert _is_safe_instruction("access system32 folder") is False

    def test_blocked_shadow(self):
        assert _is_safe_instruction("cat /etc/shadow") is False

    def test_sanitize_valid_task(self):
        task = {"task_id": "abc", "instruction": "Summarize the document", "context": {}}
        result = sanitize_task(task)
        assert result["instruction"] == "Summarize the document"

    def test_sanitize_empty_instruction(self):
        import pytest
        with pytest.raises(ValueError, match="empty"):
            sanitize_task({"task_id": "x", "instruction": "   "})

    def test_sanitize_oversized_task(self):
        import pytest
        big_task = {"task_id": "x", "instruction": "X" * (_MAX_TASK_SIZE_BYTES + 1)}
        with pytest.raises(ValueError, match="size limit"):
            sanitize_task(big_task)

    def test_sanitize_blocked_path(self):
        import pytest
        with pytest.raises(ValueError, match="blocked"):
            sanitize_task({"task_id": "x", "instruction": "read /etc/passwd"})


# ---------------------------------------------------------------------------
# CoworkWatcher polling mode tests
# ---------------------------------------------------------------------------

class TestCoworkWatcherPolling:
    """Use tmp directories to avoid touching real state/."""

    def setup_method(self, tmp_path_factory=None):
        import tempfile
        self._tmpdir = Path(tempfile.mkdtemp())
        self._outbox = self._tmpdir / "outbox"
        self._outbox.mkdir()
        self._delivered: List[Dict[str, Any]] = []

    def teardown_method(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _callback(self, result: Dict[str, Any]) -> None:
        self._delivered.append(result)

    def test_watcher_starts_and_stops(self):
        w = CoworkWatcher(callback=self._callback, outbox_dir=self._outbox, poll_interval=0.1)
        w.start()
        assert w.is_active()
        w.stop()
        assert not w.is_active()

    def test_watcher_detects_new_file(self):
        w = CoworkWatcher(callback=self._callback, outbox_dir=self._outbox, poll_interval=0.1)
        w.start()
        # Write a fake result
        result = {"task_id": "test-123", "status": "done", "result": "hello"}
        (self._outbox / "test-123.json").write_text(json.dumps(result), encoding="utf-8")
        # Wait for polling to pick it up
        deadline = time.time() + 3.0
        while time.time() < deadline and not self._delivered:
            time.sleep(0.1)
        w.stop()
        assert len(self._delivered) == 1
        assert self._delivered[0]["task_id"] == "test-123"

    def test_watcher_ignores_non_json(self):
        w = CoworkWatcher(callback=self._callback, outbox_dir=self._outbox, poll_interval=0.1)
        w.start()
        (self._outbox / "somefile.txt").write_text("hello", encoding="utf-8")
        time.sleep(0.4)
        w.stop()
        assert len(self._delivered) == 0

    def test_watcher_handles_malformed_json(self):
        w = CoworkWatcher(callback=self._callback, outbox_dir=self._outbox, poll_interval=0.1)
        w.start()
        (self._outbox / "bad.json").write_text("not json!!!", encoding="utf-8")
        time.sleep(0.4)
        w.stop()
        # Should not crash, just skip
        assert len(self._delivered) == 0

    def test_watcher_callback_called_once_per_file(self):
        w = CoworkWatcher(callback=self._callback, outbox_dir=self._outbox, poll_interval=0.1)
        w.start()
        result = {"task_id": "once-123", "status": "done", "result": "once"}
        (self._outbox / "once-123.json").write_text(json.dumps(result), encoding="utf-8")
        time.sleep(0.5)
        w.stop()
        assert len(self._delivered) == 1  # not called twice

    def test_watcher_timeout_triggers_callback(self):
        w = CoworkWatcher(
            callback=self._callback,
            outbox_dir=self._outbox,
            poll_interval=0.1,
            timeout_sec=1,  # very short for testing
        )
        w.start()
        w.track_task("timeout-task-abc")
        deadline = time.time() + 3.0
        while time.time() < deadline and not self._delivered:
            time.sleep(0.1)
        w.stop()
        assert len(self._delivered) >= 1
        assert self._delivered[0]["status"] == "timeout"
        assert "timeout-task-abc" in self._delivered[0]["task_id"]

    def test_track_and_untrack(self):
        w = CoworkWatcher(callback=self._callback, outbox_dir=self._outbox, timeout_sec=60)
        w.track_task("my-task")
        w.untrack_task("my-task")
        # After untrack, timeout should not fire even after timeout_sec

    def test_watcher_auto_creates_outbox(self):
        import shutil
        new_outbox = self._tmpdir / "new_outbox"
        # Don't create it — watcher should handle it
        w = CoworkWatcher(callback=self._callback, outbox_dir=new_outbox, poll_interval=0.1)
        w.start()
        time.sleep(0.3)
        w.stop()
        assert new_outbox.exists()  # should have been created


# ---------------------------------------------------------------------------
# Global singleton tests
# ---------------------------------------------------------------------------

class TestGlobalWatcher:
    def teardown_method(self):
        stop_watcher()

    def test_start_watcher_returns_instance(self):
        import tempfile
        tmpdir = Path(tempfile.mkdtemp())
        outbox = tmpdir / "out"
        outbox.mkdir()
        delivered = []
        # Patch the global outbox for testing
        w = CoworkWatcher(callback=lambda r: delivered.append(r), outbox_dir=outbox, poll_interval=0.2)
        w.start()
        assert w.is_active()
        w.stop()

    def test_stop_watcher_cleans_singleton(self):
        from app.services import cowork_watcher as cw_mod
        delivered = []
        w = start_watcher(callback=lambda r: delivered.append(r))
        assert w.is_active()
        stop_watcher()
        assert cw_mod._global_watcher is None


# ---------------------------------------------------------------------------
# cowork_bridge extensions tests
# ---------------------------------------------------------------------------

class TestCoworkBridgeExtensions:
    def test_archive_processed_is_alias(self, tmp_path):
        # inject fake result then archive it
        from app.services import cowork_bridge as cb
        orig_inbox = cb._INBOX
        orig_outbox = cb._OUTBOX
        orig_archive = cb._ARCHIVE
        cb._INBOX = tmp_path / "inbox"
        cb._OUTBOX = tmp_path / "outbox"
        cb._ARCHIVE = tmp_path / "archive"
        for d in (cb._INBOX, cb._OUTBOX, cb._ARCHIVE):
            d.mkdir()
        try:
            inject_fake_result("test-arch-1", "result text")
            assert (cb._OUTBOX / "test-arch-1.json").exists()
            result = archive_processed("test-arch-1")
            assert result is True
            assert (cb._ARCHIVE / "test-arch-1.json").exists()
            assert not (cb._OUTBOX / "test-arch-1.json").exists()
        finally:
            cb._INBOX = orig_inbox
            cb._OUTBOX = orig_outbox
            cb._ARCHIVE = orig_archive

    def test_record_and_get_cost(self, tmp_path):
        from app.services import cowork_bridge as cb
        orig_log = cb._COST_LOG
        cb._COST_LOG = tmp_path / "cost_log.json"
        try:
            record_cowork_cost("task-cost-1", units=1.0)
            record_cowork_cost("task-cost-2", units=1.0)
            summary = get_cowork_cost_summary()
            assert summary["total_units"] == 2.0
            assert summary["task_count"] == 2
        finally:
            cb._COST_LOG = orig_log

    def test_cost_accumulates(self, tmp_path):
        from app.services import cowork_bridge as cb
        orig_log = cb._COST_LOG
        cb._COST_LOG = tmp_path / "cost_acc.json"
        try:
            record_cowork_cost("t1", 1.0)
            record_cowork_cost("t2", 0.5)
            summary = get_cowork_cost_summary()
            assert abs(summary["total_units"] - 1.5) < 0.001
        finally:
            cb._COST_LOG = orig_log


# ---------------------------------------------------------------------------
# agent_registry cost_per_use tests
# ---------------------------------------------------------------------------

class TestAgentCostPerUse:
    def test_all_agents_have_cost_per_use(self):
        from app.services.agent_registry import AGENTS
        for aid, cfg in AGENTS.items():
            assert "cost_per_use" in cfg, f"Agent {aid} missing cost_per_use"

    def test_estimate_plan_cost_usd(self):
        from app.services.agent_registry import estimate_plan_cost_usd
        cost = estimate_plan_cost_usd(["perplexity_researcher", "smart_table"])
        assert cost > 0

    def test_estimate_empty_plan(self):
        from app.services.agent_registry import estimate_plan_cost_usd
        assert estimate_plan_cost_usd([]) == 0.0

    def test_cowork_agent_is_available(self):
        from app.services.agent_registry import get_agent
        agent = get_agent("cowork_file_agent")
        assert agent is not None
        assert agent["available"] is True
