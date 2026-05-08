from __future__ import annotations

import json
import time
from pathlib import Path

from .models import MissionSnapshot


class SmartMemoryManager:
    def __init__(self, base_dir: Path, obsidian_vault: Path | None = None) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir = self.base_dir / "snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

        self.obsidian_vault = Path(obsidian_vault) if obsidian_vault else None
        if self.obsidian_vault:
            self.obsidian_vault.mkdir(parents=True, exist_ok=True)

    def persist_snapshot(self, snapshot: MissionSnapshot) -> Path:
        dest = self.snapshots_dir / f"{snapshot.mission_id}.json"
        dest.write_text(
            json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return dest

    def load_snapshot(self, mission_id: str) -> MissionSnapshot | None:
        dest = self.snapshots_dir / f"{mission_id}.json"
        if not dest.exists():
            return None
        return MissionSnapshot.model_validate(json.loads(dest.read_text(encoding="utf-8")))

    def build_context_bundle(self, snapshot: MissionSnapshot, limit_recent: int = 12) -> dict:
        recent = snapshot.audit_log[-limit_recent:]
        completed = [t.task.task_id for t in snapshot.tasks if t.status == "completed"]
        failed = [t.task.task_id for t in snapshot.tasks if t.status == "failed"]
        return {
            "mission_id": snapshot.mission_id,
            "goal": snapshot.goal,
            "status": snapshot.status,
            "recent_events": recent,
            "completed_tasks": completed,
            "failed_tasks": failed,
            "artifact_count": len(snapshot.artifacts),
        }

    def write_obsidian_note(self, title: str, body: str) -> Path | None:
        if not self.obsidian_vault:
            return None
        safe_name = "".join(c if c.isalnum() or c in ("_", "-", " ") else "_" for c in title).strip() or "note"
        dest = self.obsidian_vault / f"{safe_name}.md"
        dest.write_text(body, encoding="utf-8")
        return dest

    def append_mission_note(self, snapshot: MissionSnapshot, body: str) -> Path | None:
        if not self.obsidian_vault:
            return None
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        content = f"# {snapshot.mission_id}\n\nGoal: {snapshot.goal}\n\nUpdated: {ts}\n\n{body}\n"
        return self.write_obsidian_note(snapshot.mission_id, content)