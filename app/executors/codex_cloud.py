from __future__ import annotations

from app.executors.base import BaseExecutor
from app.models import TaskExecutionResult


class CodexCloudExecutor(BaseExecutor):
    """
    Placeholder adapter for future Codex cloud execution.

    This intentionally returns a structured failure instead of pretending
    that cloud execution is already wired up.
    """

    def run(self, task: dict) -> TaskExecutionResult:
        return TaskExecutionResult(
            success=False,
            error=(
                "codex_cloud is not wired in this scaffold yet. "
                "Use codex_local for a real local Codex CLI integration, "
                "or extend this executor with your preferred cloud bridge."
            ),
            result={"task_id": task["task_id"]},
        )
