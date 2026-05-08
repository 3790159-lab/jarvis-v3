from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


class JarvisAutonomousTaskSeeder:
    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()
        self.root = self.project_root / "jarvis_stage3_artifacts" / "autonomous_task_seeder"
        self.root.mkdir(parents=True, exist_ok=True)
        self.queue_path = self.root / "night_task_queue.json"

    def _load(self) -> List[str]:
        if self.queue_path.exists():
            try:
                return json.loads(self.queue_path.read_text(encoding="utf-8"))
            except Exception:
                return []
        return []

    def _save(self, tasks: List[str]) -> None:
        self.queue_path.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")

    def seed_from_result(self, previous_task: str, result_status: str, lane: str = "") -> List[str]:
        tasks = self._load()
        new_tasks: List[str] = []

        t = (previous_task or "").lower()

        if "utf" in t or "error" in t or "ошиб" in t:
            new_tasks.append("Autonomous follow-up: audit latest logs for recurring UTF-8/errors and generate safer report formatting.")

        if "memory" in t or "learning" in t:
            new_tasks.append("Autonomous follow-up: summarize latest execution lessons and propose next safe improvements.")

        if "service connector" in t or "google sheets" in t or "gmail" in t:
            new_tasks.append("Autonomous follow-up: verify connector registry readiness and identify missing credentials without exposing secrets.")

        if "telegram" in t:
            new_tasks.append("Autonomous follow-up: improve Telegram UX for task status, completion notifications and truth-guarded answers.")

        if "n8n" in t or "pipeline" in t:
            new_tasks.append("Autonomous follow-up: inspect latest n8n workflow artifacts and propose safer dynamic pipeline templates.")

        if result_status not in {"completed", "tested"}:
            new_tasks.append("Autonomous follow-up: investigate failed/degraded iteration and create rollback-safe fix plan.")

        if not new_tasks:
            new_tasks.append("Autonomous follow-up: inspect system health, recent artifacts and choose the next highest-impact low-risk improvement.")

        for task in new_tasks:
            if task not in tasks:
                tasks.append(task)

        # keep queue bounded
        tasks = tasks[-50:]
        self._save(tasks)

        event = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "previous_task": previous_task,
            "status": result_status,
            "lane": lane,
            "new_tasks": new_tasks,
        }
        with (self.root / "seed_events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

        return new_tasks

    def next_task(self, fallback: str) -> str:
        tasks = self._load()
        if not tasks:
            return fallback
        task = tasks.pop(0)
        self._save(tasks)
        return task