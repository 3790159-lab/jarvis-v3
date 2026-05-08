"""Phase 19 prep: Cowork Bridge tests."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


# ---------------------------------------------------------------------------
# Fixture: temporary cowork directories
# ---------------------------------------------------------------------------

@pytest.fixture
def cowork_dirs(monkeypatch, tmp_path):
    """Redirect inbox/outbox/archive to tmp_path for isolation."""
    inbox = tmp_path / "inbox"
    outbox = tmp_path / "outbox"
    archive = tmp_path / "archive"
    inbox.mkdir()
    outbox.mkdir()
    archive.mkdir()

    import app.services.cowork_bridge as mod
    monkeypatch.setattr(mod, "_INBOX", inbox)
    monkeypatch.setattr(mod, "_OUTBOX", outbox)
    monkeypatch.setattr(mod, "_ARCHIVE", archive)
    return {"inbox": inbox, "outbox": outbox, "archive": archive, "mod": mod}


# ---------------------------------------------------------------------------
# make_task
# ---------------------------------------------------------------------------

def test_make_task_has_required_fields(cowork_dirs):
    from app.services.cowork_bridge import make_task
    task = make_task("Organize PDFs in Downloads")
    for field in ("task_id", "instruction", "context", "callback_marker", "submitted_at", "deadline", "status"):
        assert field in task, f"Missing field: {field}"


def test_make_task_status_is_pending(cowork_dirs):
    from app.services.cowork_bridge import make_task
    task = make_task("test instruction")
    assert task["status"] == "pending"


def test_make_task_unique_ids(cowork_dirs):
    from app.services.cowork_bridge import make_task
    ids = {make_task("test")["task_id"] for _ in range(5)}
    assert len(ids) == 5


def test_make_task_with_context(cowork_dirs):
    from app.services.cowork_bridge import make_task
    task = make_task("instruction", context={"folder": "/tmp"})
    assert task["context"]["folder"] == "/tmp"


# ---------------------------------------------------------------------------
# send_task_to_cowork
# ---------------------------------------------------------------------------

def test_send_task_writes_json_to_inbox(cowork_dirs):
    mod = cowork_dirs["mod"]
    task = mod.make_task("Organize files")
    task_id = mod.send_task_to_cowork(task)
    inbox_file = cowork_dirs["inbox"] / f"{task_id}.json"
    assert inbox_file.exists()


def test_send_task_json_is_valid(cowork_dirs):
    mod = cowork_dirs["mod"]
    task = mod.make_task("Test task")
    task_id = mod.send_task_to_cowork(task)
    data = json.loads((cowork_dirs["inbox"] / f"{task_id}.json").read_text())
    assert data["task_id"] == task_id


def test_send_task_returns_task_id(cowork_dirs):
    mod = cowork_dirs["mod"]
    task = mod.make_task("task")
    tid = mod.send_task_to_cowork(task)
    assert tid == task["task_id"]


# ---------------------------------------------------------------------------
# inject_fake_result + get_task_result
# ---------------------------------------------------------------------------

def test_get_task_result_none_when_no_result(cowork_dirs):
    mod = cowork_dirs["mod"]
    assert mod.get_task_result("nonexistent-uuid") is None


def test_get_task_result_returns_result_after_inject(cowork_dirs):
    mod = cowork_dirs["mod"]
    task = mod.make_task("test")
    tid = mod.send_task_to_cowork(task)
    mod.inject_fake_result(tid, "All PDFs organized!")
    result = mod.get_task_result(tid)
    assert result is not None
    assert result["result"] == "All PDFs organized!"


def test_inject_fake_result_creates_outbox_file(cowork_dirs):
    mod = cowork_dirs["mod"]
    mod.inject_fake_result("test-uuid", "done")
    outbox_file = cowork_dirs["outbox"] / "test-uuid.json"
    assert outbox_file.exists()


# ---------------------------------------------------------------------------
# poll_cowork_results
# ---------------------------------------------------------------------------

def test_poll_results_empty_when_no_files(cowork_dirs):
    mod = cowork_dirs["mod"]
    results = mod.poll_cowork_results()
    assert results == []


def test_poll_results_returns_multiple(cowork_dirs):
    mod = cowork_dirs["mod"]
    for i in range(3):
        mod.inject_fake_result(f"uuid-{i}", f"result {i}")
    results = mod.poll_cowork_results()
    assert len(results) == 3


# ---------------------------------------------------------------------------
# mark_result_processed
# ---------------------------------------------------------------------------

def test_mark_processed_moves_to_archive(cowork_dirs):
    mod = cowork_dirs["mod"]
    mod.inject_fake_result("arch-uuid", "done")
    moved = mod.mark_result_processed("arch-uuid")
    assert moved is True
    assert not (cowork_dirs["outbox"] / "arch-uuid.json").exists()
    assert (cowork_dirs["archive"] / "arch-uuid.json").exists()


def test_mark_processed_returns_false_if_not_found(cowork_dirs):
    mod = cowork_dirs["mod"]
    result = mod.mark_result_processed("nonexistent-uuid-xyz")
    assert result is False


# ---------------------------------------------------------------------------
# list_pending_tasks + cancel_task
# ---------------------------------------------------------------------------

def test_list_pending_tasks(cowork_dirs):
    mod = cowork_dirs["mod"]
    task = mod.make_task("pending task")
    mod.send_task_to_cowork(task)
    pending = mod.list_pending_tasks()
    assert len(pending) == 1
    assert pending[0]["task_id"] == task["task_id"]


def test_cancel_task_removes_from_inbox(cowork_dirs):
    mod = cowork_dirs["mod"]
    task = mod.make_task("to cancel")
    tid = mod.send_task_to_cowork(task)
    result = mod.cancel_task(tid)
    assert result is True
    assert not (cowork_dirs["inbox"] / f"{tid}.json").exists()


def test_cancel_task_not_found(cowork_dirs):
    mod = cowork_dirs["mod"]
    assert mod.cancel_task("doesnt-exist") is False
