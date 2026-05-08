from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.models import IntakeRequest, IntakeResponse, TaskRecord, TaskStatus


@dataclass
class Plan:
    strategy: str


class SupervisorService:
    def __init__(self) -> None:
        self.tasks: dict[str, TaskRecord] = {}

    def intake(self, request: IntakeRequest) -> IntakeResponse:
        text = (request.user_request or "").lower()
        task = self._make_task(text, request.project_root)
        self.tasks[task.task_id] = task
        self._run_task(task, request.project_root)
        return IntakeResponse(
            plan=Plan(strategy="local_supervisor"),
            tasks=[task],
        )

    def get_task(self, task_id: str) -> TaskRecord | None:
        return self.tasks.get(task_id)

    def _make_task(self, text: str, project_root: str | None) -> TaskRecord:
        task_id = f"task_{uuid.uuid4().hex[:8]}"

        if "git status" in text:
            return TaskRecord(
                task_id=task_id,
                title="git status",
                type="shell_git",
                status=TaskStatus.pending,
                result={},
                error=None,
            )

        if "python" in text and ("версию" in text or "version" in text):
            return TaskRecord(
                task_id=task_id,
                title="python version",
                type="shell_test",
                status=TaskStatus.pending,
                result={},
                error=None,
            )

        if "файлы" in text or "files" in text or "папк" in text:
            return TaskRecord(
                task_id=task_id,
                title="list project files",
                type="shell_diagnostic",
                status=TaskStatus.pending,
                result={},
                error=None,
            )

        if "codex" in text or "аудит" in text or "проанализируй проект" in text:
            return TaskRecord(
                task_id=task_id,
                title="codex audit",
                type="codex_audit",
                status=TaskStatus.pending,
                result={"message": "Codex integration placeholder is ready for next step."},
                error=None,
            )

        return TaskRecord(
            task_id=task_id,
            title="unmapped task",
            type="shell_diagnostic",
            status=TaskStatus.pending,
            result={"message": f"Task accepted but not mapped yet: {text}"},
            error=None,
        )

    def _run_task(self, task: TaskRecord, project_root: str | None) -> None:
        root = Path(project_root) if project_root else Path.cwd()

        try:
            if task.type == "shell_git":
                result = subprocess.run(
                    ["git", "status", "--short"],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=20,
                    shell=False,
                )
                task.result = {"stdout": result.stdout, "stderr": result.stderr}
                task.status = TaskStatus.succeeded if result.returncode == 0 else TaskStatus.failed
                if result.returncode != 0:
                    task.error = result.stderr.strip() or "git status failed"
                return

            if task.type == "shell_test":
                result = subprocess.run(
                    ["python", "--version"],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    shell=False,
                )
                stdout = result.stdout or result.stderr
                task.result = {"stdout": stdout, "stderr": result.stderr}
                task.status = TaskStatus.succeeded if result.returncode == 0 else TaskStatus.failed
                if result.returncode != 0:
                    task.error = result.stderr.strip() or "python version check failed"
                return

            if task.type == "shell_diagnostic":
                files = [{"Name": p.name} for p in root.iterdir()]
                task.result = {"stdout": json.dumps(files, ensure_ascii=False)}
                task.status = TaskStatus.succeeded
                return

            if task.type.startswith("codex"):
                task.status = TaskStatus.succeeded
                return

            task.status = TaskStatus.failed
            task.error = "Unknown task type"

        except Exception as exc:
            task.status = TaskStatus.failed
            task.error = str(exc)
            task.result = {"stderr": str(exc)}