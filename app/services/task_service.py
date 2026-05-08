from __future__ import annotations

import uuid
from typing import Any


class TaskService:
    def create_goal_id(self) -> str:
        return f"goal_{uuid.uuid4().hex[:8]}"

    def create_mission_id(self) -> str:
        return f"mission_{uuid.uuid4().hex[:8]}"

    def create_task_id(self) -> str:
        return f"task_{uuid.uuid4().hex[:8]}"

    def build_initial_tasks(self, objective: str, constraints: dict[str, Any]) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []

        tasks.append({
            "task_id": self.create_task_id(),
            "type": "interpret_goal",
            "status": "queued",
            "input": {
                "objective": objective,
                "constraints": constraints,
            },
        })

        tasks.append({
            "task_id": self.create_task_id(),
            "type": "build_plan",
            "status": "queued",
            "input": {
                "objective": objective,
            },
        })

        tasks.append({
            "task_id": self.create_task_id(),
            "type": "prepare_execution",
            "status": "queued",
            "input": {
                "mode": "safe",
            },
        })

        return tasks


task_service = TaskService()