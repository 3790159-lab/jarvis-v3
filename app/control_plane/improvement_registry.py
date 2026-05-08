from __future__ import annotations

import json
import time
from pathlib import Path


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        raw = path.read_text(encoding="utf-8")
        if not raw.strip():
            return default
        return json.loads(raw)
    except Exception:
        return default


def _save_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_note_unique(item: dict, note: str) -> None:
    note = str(note or "").strip()
    if not note:
        return
    notes = []
    for existing in list(item.get("notes", []) or []):
        s = str(existing).strip()
        if s and s not in notes:
            notes.append(s)
    if note not in notes:
        notes.append(note)
    item["notes"] = notes


class ImprovementRegistry:
    def __init__(self, base_dir: str | Path = "state/agent_mesh") -> None:
        self.base_dir = Path(base_dir)
        self.path = self.base_dir / "improvement_registry.json"
        self._ensure()

    def _ensure(self) -> None:
        if not self.path.exists():
            _save_json(self.path, {"items": []})

    def load(self) -> dict:
        self._ensure()
        return _load_json(self.path, {"items": []})

    def save(self, data: dict) -> None:
        for item in data.get("items", []):
            notes = []
            for n in list(item.get("notes", []) or []):
                s = str(n).strip()
                if s and s not in notes:
                    notes.append(s)
            item["notes"] = notes
        _save_json(self.path, data)

    def upsert_proposals(self, proposals: list[dict]) -> dict:
        data = self.load()
        items = data.get("items", [])
        now = time.time()

        by_id = {str(x.get("id")): x for x in items if x.get("id")}
        added = 0
        updated = 0

        for proposal in proposals:
            pid = str(proposal.get("id") or "").strip()
            if not pid:
                continue

            existing = by_id.get(pid)
            if existing:
                existing["priority"] = proposal.get("priority", existing.get("priority", "medium"))
                existing["title"] = proposal.get("title", existing.get("title", pid))
                existing["reason"] = proposal.get("reason", existing.get("reason", ""))
                existing["suggested_action"] = proposal.get("suggested_action", existing.get("suggested_action", ""))
                existing["last_seen_ts"] = now
                if existing.get("status") in {None, "", "retired"}:
                    existing["status"] = "proposed"
                updated += 1
            else:
                items.append({
                    "id": pid,
                    "priority": proposal.get("priority", "medium"),
                    "title": proposal.get("title", pid),
                    "reason": proposal.get("reason", ""),
                    "suggested_action": proposal.get("suggested_action", ""),
                    "status": "proposed",
                    "first_seen_ts": now,
                    "last_seen_ts": now,
                    "applied_ts": 0.0,
                    "retired_ts": 0.0,
                    "notes": [],
                })
                added += 1

        data["items"] = items
        self.save(data)
        return {"added": added, "updated": updated, "count": len(items)}

    def list_items(self) -> list[dict]:
        data = self.load()
        rows = list(data.get("items", []))
        for row in rows:
            notes = []
            for n in list(row.get("notes", []) or []):
                s = str(n).strip()
                if s and s not in notes:
                    notes.append(s)
            row["notes"] = notes
        rows.sort(key=lambda x: (str(x.get("status", "")), -(float(x.get("last_seen_ts", 0.0) or 0.0))))
        return rows

    def mark_applied(self, improvement_id: str, note: str = "") -> dict | None:
        data = self.load()
        for item in data.get("items", []):
            if str(item.get("id")) == improvement_id:
                item["status"] = "applied"
                item["applied_ts"] = time.time()
                _append_note_unique(item, note)
                self.save(data)
                return item
        return None

    def mark_active(self, improvement_id: str, note: str = "") -> dict | None:
        data = self.load()
        for item in data.get("items", []):
            if str(item.get("id")) == improvement_id:
                item["status"] = "active"
                _append_note_unique(item, note)
                self.save(data)
                return item
        return None

    def mark_retired(self, improvement_id: str, note: str = "") -> dict | None:
        data = self.load()
        for item in data.get("items", []):
            if str(item.get("id")) == improvement_id:
                item["status"] = "retired"
                item["retired_ts"] = time.time()
                _append_note_unique(item, note)
                self.save(data)
                return item
        return None

    def auto_retire_stale(self, active_ids: set[str], stale_seconds: int = 86400) -> dict:
        data = self.load()
        now = time.time()
        retired = 0

        for item in data.get("items", []):
            pid = str(item.get("id") or "")
            if not pid:
                continue
            if pid not in active_ids and item.get("status") in {"proposed", "applied", "active"}:
                last_seen = float(item.get("last_seen_ts", 0.0) or 0.0)
                if last_seen and (now - last_seen) >= stale_seconds:
                    item["status"] = "retired"
                    item["retired_ts"] = now
                    _append_note_unique(item, "Auto-retired as stale / no longer proposed.")
                    retired += 1

        self.save(data)
        return {"retired": retired, "count": len(data.get("items", []))}