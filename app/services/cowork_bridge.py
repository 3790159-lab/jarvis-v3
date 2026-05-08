"""Phase 19 (Block D1): Cowork Bridge — filesystem bridge for Claude Desktop delegation.

Jarvis writes task JSON to state/cowork_inbox/<task_id>.json.
Claude Desktop (Cowork) watches the folder, executes tasks, writes results to
state/cowork_outbox/<task_id>.json.
Jarvis polls outbox and delivers results back to the user.

Task lifecycle: submit → wait → poll → deliver → archive
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).parent.parent.parent
_INBOX = ROOT / "state" / "cowork_inbox"
_OUTBOX = ROOT / "state" / "cowork_outbox"
_ARCHIVE = ROOT / "state" / "cowork_archive"

for _d in (_INBOX, _OUTBOX, _ARCHIVE):
    _d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Task format
# ---------------------------------------------------------------------------

def make_task(
    instruction: str,
    context: Optional[Dict[str, Any]] = None,
    deadline_sec: int = 300,
) -> Dict[str, Any]:
    """Create a task dict ready to be sent to Cowork."""
    return {
        "task_id": str(uuid.uuid4()),
        "instruction": instruction,
        "context": context or {},
        "callback_marker": f"jarvis_cb_{int(time.time())}",
        "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "deadline": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(time.time() + deadline_sec),
        ),
        "status": "pending",
    }


# ---------------------------------------------------------------------------
# Send / poll / mark
# ---------------------------------------------------------------------------

def send_task_to_cowork(task: Dict[str, Any]) -> str:
    """Write task JSON to inbox. Returns task_id."""
    task_id = task["task_id"]
    path = _INBOX / f"{task_id}.json"
    path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    return task_id


def poll_cowork_results(max_results: int = 10) -> List[Dict[str, Any]]:
    """Return all completed results from outbox (parsed JSON). Does NOT remove them."""
    results = []
    for f in sorted(_OUTBOX.glob("*.json"))[:max_results]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            data["_result_file"] = str(f)
            results.append(data)
        except Exception:
            pass
    return results


def get_task_result(task_id: str) -> Optional[Dict[str, Any]]:
    """Return result for a specific task_id, or None if not ready."""
    path = _OUTBOX / f"{task_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def mark_result_processed(task_id: str) -> bool:
    """Move result from outbox to archive. Returns True if moved."""
    src = _OUTBOX / f"{task_id}.json"
    if not src.exists():
        return False
    dst = _ARCHIVE / f"{task_id}.json"
    src.rename(dst)
    return True


def list_pending_tasks() -> List[Dict[str, Any]]:
    """Return tasks in inbox that haven't been processed yet."""
    tasks = []
    for f in sorted(_INBOX.glob("*.json")):
        try:
            tasks.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return tasks


def cancel_task(task_id: str) -> bool:
    """Remove task from inbox (if not yet picked up). Returns True if removed."""
    path = _INBOX / f"{task_id}.json"
    if path.exists():
        path.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# High-level: submit and wait (blocking, with timeout)
# ---------------------------------------------------------------------------

def submit_and_wait(
    instruction: str,
    context: Optional[Dict[str, Any]] = None,
    timeout_sec: int = 120,
    poll_interval: float = 3.0,
) -> Optional[Dict[str, Any]]:
    """Submit task and block until result arrives or timeout."""
    task = make_task(instruction, context, deadline_sec=timeout_sec)
    task_id = send_task_to_cowork(task)
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        result = get_task_result(task_id)
        if result is not None:
            mark_result_processed(task_id)
            return result
        time.sleep(poll_interval)
    return None


# ---------------------------------------------------------------------------
# Fake result injection (for testing / manual simulation)
# ---------------------------------------------------------------------------

def archive_processed(task_id: str) -> bool:
    """Alias for mark_result_processed — move result to archive."""
    return mark_result_processed(task_id)


def inject_fake_result(task_id: str, result_text: str) -> None:
    """Write a fake result to outbox — used for testing Cowork integration."""
    path = _OUTBOX / f"{task_id}.json"
    path.write_text(
        json.dumps({
            "task_id": task_id,
            "status": "done",
            "result": result_text,
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Cost tracking (1 unit per Cowork task, for Block D2 limits)
# ---------------------------------------------------------------------------

_COST_LOG = ROOT / "state" / "cowork_cost_log.json"


def _load_cost_log() -> Dict[str, Any]:
    if _COST_LOG.exists():
        try:
            return json.loads(_COST_LOG.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"total_units": 0, "tasks": []}


def _save_cost_log(log: Dict[str, Any]) -> None:
    _COST_LOG.parent.mkdir(parents=True, exist_ok=True)
    _COST_LOG.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


def record_cowork_cost(task_id: str, units: float = 1.0) -> None:
    """Record 1 cost unit for a Cowork task."""
    log = _load_cost_log()
    log["total_units"] = log.get("total_units", 0) + units
    log.setdefault("tasks", []).append({
        "task_id": task_id,
        "units": units,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    _save_cost_log(log)


def get_cowork_cost_summary() -> Dict[str, Any]:
    """Return total cost units and count for dashboard."""
    log = _load_cost_log()
    return {
        "total_units": log.get("total_units", 0),
        "task_count": len(log.get("tasks", [])),
    }
