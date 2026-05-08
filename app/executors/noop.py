from __future__ import annotations

from app.executors.base import BaseExecutor
from app.models import TaskExecutionResult


class NoopExecutor(BaseExecutor):
    def run(self, task: dict) -> TaskExecutionResult:
        return TaskExecutionResult(
            success=True,
            result={
                "message": "Noop executor completed successfully.",
                "task_id": task["task_id"],
                "payload": task.get("payload", {}),
            },
        )
