# -*- coding: utf-8 -*-
"""Figma design queue — persists design briefs and tracks MCP processing status."""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent.parent
_QUEUE_DIR = _ROOT / "state" / "figma_queue"
_LOG_FILE = _QUEUE_DIR / "log.jsonl"

STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


def _queue_dir() -> Path:
    _QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    return _QUEUE_DIR


def _item_path(queue_id: str) -> Path:
    return _queue_dir() / f"{queue_id}.json"


def _load(queue_id: str) -> Optional[Dict[str, Any]]:
    from app.services.block_l_common import load_json_safe
    return load_json_safe(_item_path(queue_id))


def _save(item: Dict[str, Any]) -> None:
    from app.services.block_l_common import save_json_safe
    save_json_safe(_item_path(item["id"]), item)


def _log(action: str, queue_id: str, extra: Optional[Dict] = None) -> None:
    import json
    _queue_dir()
    entry: Dict[str, Any] = {
        "ts": datetime.utcnow().isoformat(),
        "action": action,
        "id": queue_id,
    }
    if extra:
        entry.update(extra)
    try:
        with _LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


class FigmaQueue:
    """Manages the Figma design queue in state/figma_queue/."""

    def add(self, brief: Dict[str, Any]) -> str:
        """Add a new design brief to the queue. Returns queue_id."""
        from datetime import datetime
        queue_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")[:19]
        item = {
            "id": queue_id,
            "brief": brief,
            "status": STATUS_PENDING,
            "created_at": datetime.utcnow().isoformat(),
            "processed_at": None,
            "figma_url": None,
            "error": None,
        }
        _save(item)
        _log("added", queue_id)
        return queue_id

    def get_pending(self) -> List[Dict[str, Any]]:
        """Return all pending items, oldest first."""
        items = []
        for f in sorted(_queue_dir().glob("*.json"), key=lambda p: p.stat().st_mtime):
            from app.services.block_l_common import load_json_safe
            data = load_json_safe(f)
            if data and data.get("status") == STATUS_PENDING:
                items.append(data)
        return items

    def get_by_id(self, queue_id: str) -> Optional[Dict[str, Any]]:
        return _load(queue_id)

    def mark_processing(self, queue_id: str) -> bool:
        item = _load(queue_id)
        if not item:
            return False
        item["status"] = STATUS_PROCESSING
        _save(item)
        _log("processing", queue_id)
        return True

    def mark_completed(self, queue_id: str, figma_url: str = "") -> bool:
        item = _load(queue_id)
        if not item:
            return False
        item["status"] = STATUS_COMPLETED
        item["figma_url"] = figma_url
        item["processed_at"] = datetime.utcnow().isoformat()
        _save(item)
        _log("completed", queue_id, {"figma_url": figma_url})
        return True

    def mark_failed(self, queue_id: str, error: str) -> bool:
        item = _load(queue_id)
        if not item:
            return False
        item["status"] = STATUS_FAILED
        item["error"] = error
        item["processed_at"] = datetime.utcnow().isoformat()
        _save(item)
        _log("failed", queue_id, {"error": error})
        return True

    def get_status_summary(self) -> Dict[str, int]:
        counts = {STATUS_PENDING: 0, STATUS_PROCESSING: 0, STATUS_COMPLETED: 0, STATUS_FAILED: 0}
        for f in _queue_dir().glob("*.json"):
            from app.services.block_l_common import load_json_safe
            data = load_json_safe(f)
            if data:
                status = data.get("status", STATUS_PENDING)
                if status in counts:
                    counts[status] += 1
        return counts

    def get_recent(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Return up to limit most recent items (any status), newest first."""
        items = []
        for f in sorted(_queue_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            from app.services.block_l_common import load_json_safe
            data = load_json_safe(f)
            if data:
                items.append(data)
            if len(items) >= limit:
                break
        return items

    def clear_completed(self, older_than_days: int = 7) -> int:
        """Delete completed items older than N days. Returns count removed."""
        cutoff = datetime.utcnow() - timedelta(days=older_than_days)
        removed = 0
        for f in list(_queue_dir().glob("*.json")):
            from app.services.block_l_common import load_json_safe
            data = load_json_safe(f)
            if not data:
                continue
            if data.get("status") != STATUS_COMPLETED:
                continue
            processed = data.get("processed_at")
            if processed:
                try:
                    dt = datetime.fromisoformat(processed)
                    if dt < cutoff:
                        f.unlink()
                        removed += 1
                except Exception:
                    pass
        return removed
