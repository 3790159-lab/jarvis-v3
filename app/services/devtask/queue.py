# -*- coding: utf-8 -*-
"""Dev-task queue — persists dev-task state and tracks status.

Mirrors ``app/services/bolt_queue.py`` (JSON per item + append log). The base
directory is injectable so tests run entirely on tmp with no real state dir.

Single-flight (see plan Допущение 2): at most one task in ``running`` OR
``awaiting_review`` at a time — ``active()`` returns it (or None). The wiring
layer enforces the guard; the queue only reports.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_AWAITING_REVIEW = "awaiting_review"
STATUS_MERGED = "merged"
STATUS_ROLLED_BACK = "rolled_back"
STATUS_FAILED = "failed"

_ACTIVE_STATUSES = (STATUS_RUNNING, STATUS_AWAITING_REVIEW)
_TERMINAL_STATUSES = (STATUS_MERGED, STATUS_ROLLED_BACK, STATUS_FAILED)

_DEFAULT_BASE = Path("state") / "dev_tasks"


class DevTaskQueue:
    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self._base = Path(base_dir) if base_dir is not None else _DEFAULT_BASE

    # ── paths ──────────────────────────────────────────────────────────────
    def _dir(self) -> Path:
        self._base.mkdir(parents=True, exist_ok=True)
        return self._base

    def _item_path(self, task_id: str) -> Path:
        return self._dir() / f"{task_id}.json"

    def _log_path(self) -> Path:
        return self._dir() / "log.jsonl"

    # ── io ─────────────────────────────────────────────────────────────────
    def _save(self, item: Dict[str, Any]) -> None:
        from app.services.block_l_common import save_json_safe
        save_json_safe(self._item_path(item["id"]), item)

    def _log(self, action: str, task_id: str, extra: Optional[Dict] = None) -> None:
        entry: Dict[str, Any] = {"ts": datetime.utcnow().isoformat(), "action": action, "id": task_id}
        if extra:
            entry.update(extra)
        try:
            with self._log_path().open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ── api ────────────────────────────────────────────────────────────────
    def add(self, desc: str, requested_by: Optional[str] = None) -> str:
        # Time prefix keeps mtime/sort order human-readable; a uuid suffix
        # guarantees uniqueness even when two adds land in the same clock tick
        # (Windows utcnow() ~15ms resolution makes bare "%f" collide).
        task_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid.uuid4().hex[:6]
        item = {
            "id": task_id,
            "desc": desc,
            "status": STATUS_QUEUED,
            "requested_by": requested_by,
            "created_at": datetime.utcnow().isoformat(),
            "worktree": None,
            "branch": f"devtask-{task_id}",
            "session_id": None,
            "base_head": None,
            "report_path": None,
            "error": None,
        }
        self._save(item)
        self._log("added", task_id, {"desc": desc})
        return task_id

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        from app.services.block_l_common import load_json_safe
        return load_json_safe(self._item_path(task_id))

    def set_status(self, task_id: str, status: str, **fields: Any) -> bool:
        item = self.get(task_id)
        if not item:
            return False
        item["status"] = status
        item.update(fields)
        self._save(item)
        self._log(status, task_id, fields or None)
        return True

    def claim(self, task_id: str) -> bool:
        """Atomically flip ``queued`` -> ``running`` exactly once, cross-process.

        Returns True if THIS caller won the claim, False if the task is missing,
        no longer ``queued``, or was already claimed by a racing handler. The
        real root of the double-fire race is >1 bot poller (separate processes),
        so an in-process lock is insufficient — the barrier is an ``O_EXCL``
        marker create, which is atomic across processes. The winner is the sole
        process that creates the marker; it alone advances the status.
        """
        item = self.get(task_id)
        if not item or item["status"] != STATUS_QUEUED:
            return False
        marker = self._dir() / f"{task_id}.claim"
        try:
            fd = os.open(str(marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False  # another handler/process already claimed this task
        os.close(fd)
        self.set_status(task_id, STATUS_RUNNING)
        return True

    def active(self) -> Optional[Dict[str, Any]]:
        """The single running/awaiting_review task, or None (single-flight)."""
        from app.services.block_l_common import load_json_safe
        for f in sorted(self._dir().glob("*.json"), key=lambda p: p.stat().st_mtime):
            data = load_json_safe(f)
            if data and data.get("status") in _ACTIVE_STATUSES:
                return data
        return None

    def month_cost(self, now: datetime) -> float:
        """Sum of ``cost`` over cards whose ``created_at`` is in ``now``'s month.

        The local CC budget ledger (Фаза 8.2): ``now`` is injected (never
        ``utcnow()`` here) so tests are deterministic. A card counts iff its
        ``created_at`` parses to the same calendar (year, month) as ``now``;
        a missing/None/unparseable ``created_at`` or ``cost`` contributes 0.
        """
        from app.services.block_l_common import load_json_safe
        total = 0.0
        for f in self._dir().glob("*.json"):
            data = load_json_safe(f)
            if not data:
                continue
            created = data.get("created_at")
            try:
                dt = datetime.fromisoformat(created)
            except (TypeError, ValueError):
                continue
            if (dt.year, dt.month) != (now.year, now.month):
                continue
            cost = data.get("cost")
            try:
                total += float(cost) if cost is not None else 0.0
            except (TypeError, ValueError):
                pass
        return total

    def list_recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        from app.services.block_l_common import load_json_safe
        items = []
        for f in sorted(self._dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            data = load_json_safe(f)
            if data:
                items.append(data)
            if len(items) >= limit:
                break
        return items
