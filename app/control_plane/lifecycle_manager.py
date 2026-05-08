from __future__ import annotations

from pathlib import Path

from .snapshot_store import SnapshotStore


class LifecycleManager:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        self.snapshot_store = SnapshotStore(self.base_dir)
        self.exec_log_path = self.base_dir / "connector_execution_log.jsonl"

    def health(self) -> dict:
        journal_files = list(self.snapshot_store.journal_dir.glob("*.jsonl"))
        exec_lines = 0
        if self.exec_log_path.exists():
            exec_lines = len(self.exec_log_path.read_text(encoding="utf-8").splitlines())

        return {
            "status": "ok",
            "active_snapshots": len(self.snapshot_store.list_active()),
            "archived_snapshots": len(self.snapshot_store.list_archived()),
            "journal_files": len(journal_files),
            "exec_log_lines": exec_lines,
        }

    def cleanup(
        self,
        retain_recent_completed: int = 10,
        max_journal_lines_per_mission: int = 400,
        max_exec_log_lines: int = 600,
    ) -> dict:
        active_files = sorted(
            self.snapshot_store.active_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        completed = []
        still_active = []

        for path in active_files:
            try:
                snap = self.snapshot_store.load_active(path.stem)
                if snap and snap.status in {"completed", "partial", "failed", "cancelled"}:
                    completed.append(path.stem)
                else:
                    still_active.append(path.stem)
            except Exception:
                still_active.append(path.stem)

        archived_count = 0
        for mission_id in completed[retain_recent_completed:]:
            if self.snapshot_store.archive(mission_id):
                archived_count += 1

        pruned_journals = 0
        for journal in self.snapshot_store.journal_dir.glob("*.jsonl"):
            try:
                lines = journal.read_text(encoding="utf-8").splitlines()
                if len(lines) > max_journal_lines_per_mission:
                    lines = lines[-max_journal_lines_per_mission:]
                    journal.write_text("\n".join(lines) + "\n", encoding="utf-8")
                    pruned_journals += 1
            except Exception:
                pass

        exec_rotated = False
        if self.exec_log_path.exists():
            try:
                lines = self.exec_log_path.read_text(encoding="utf-8").splitlines()
                if len(lines) > max_exec_log_lines:
                    lines = lines[-max_exec_log_lines:]
                    self.exec_log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                    exec_rotated = True
            except Exception:
                pass

        return {
            "status": "ok",
            "archived_count": archived_count,
            "pruned_journals": pruned_journals,
            "exec_rotated": exec_rotated,
            "active_after": len(self.snapshot_store.list_active()),
            "archived_after": len(self.snapshot_store.list_archived()),
        }

    def on_mission_finished(self) -> dict:
        return self.cleanup(
            retain_recent_completed=12,
            max_journal_lines_per_mission=500,
            max_exec_log_lines=800,
        )