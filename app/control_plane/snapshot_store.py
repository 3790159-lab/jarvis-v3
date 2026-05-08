from __future__ import annotations

import json
import time
from pathlib import Path

from .models import MissionSnapshot


class SnapshotStore:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        self.active_dir = self.base_dir / "active_snapshots"
        self.archive_dir = self.base_dir / "archive_snapshots"
        self.journal_dir = self.base_dir / "journals"
        self.active_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.journal_dir.mkdir(parents=True, exist_ok=True)

    def persist_active(self, snapshot: MissionSnapshot) -> Path:
        dest = self.active_dir / f"{snapshot.mission_id}.json"
        dest.write_text(
            json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return dest

    def load_active(self, mission_id: str) -> MissionSnapshot | None:
        dest = self.active_dir / f"{mission_id}.json"
        if not dest.exists():
            return None
        return MissionSnapshot.model_validate(json.loads(dest.read_text(encoding="utf-8")))

    def load_any(self, mission_id: str) -> MissionSnapshot | None:
        active = self.active_dir / f"{mission_id}.json"
        if active.exists():
            return MissionSnapshot.model_validate(json.loads(active.read_text(encoding="utf-8")))

        archived = self.archive_dir / f"{mission_id}.json"
        if archived.exists():
            return MissionSnapshot.model_validate(json.loads(archived.read_text(encoding="utf-8")))

        return None

    def archive(self, mission_id: str) -> bool:
        src = self.active_dir / f"{mission_id}.json"
        if not src.exists():
            return False
        dst = self.archive_dir / src.name
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        src.unlink(missing_ok=True)
        return True

    def append_journal(self, mission_id: str, event: dict) -> Path:
        dest = self.journal_dir / f"{mission_id}.jsonl"
        line = dict(event)
        line.setdefault("ts", time.strftime("%Y-%m-%d %H:%M:%S"))
        with dest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        return dest

    def list_active(self) -> list[str]:
        return sorted(p.stem for p in self.active_dir.glob("*.json"))

    def list_archived(self) -> list[str]:
        return sorted(p.stem for p in self.archive_dir.glob("*.json"))