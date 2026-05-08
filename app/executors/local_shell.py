from __future__ import annotations

import os
import subprocess
from pathlib import Path

from app.executors.base import BaseExecutor
from app.models import TaskExecutionResult
from app.settings import settings


class LocalShellExecutor(BaseExecutor):
    def run(self, task: dict) -> TaskExecutionResult:
        payload = task.get("payload", {})
        command = payload.get("command")
        if not command:
            return TaskExecutionResult(success=False, error="Missing payload.command for shell_local task.")

        working_directory = task.get("working_directory") or os.getcwd()
        cwd = Path(working_directory).resolve()

        if not cwd.exists():
            return TaskExecutionResult(success=False, error=f"Working directory does not exist: {cwd}")

        shell_name = settings.default_shell.lower()
        if shell_name == "cmd":
            exec_args = ["cmd", "/c", command]
        else:
            exec_args = ["powershell", "-NoProfile", "-Command", command]

        completed = subprocess.run(
            exec_args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=payload.get("timeout_seconds", 600),
        )

        result = {
            "command": command,
            "cwd": str(cwd),
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }

        if completed.returncode != 0:
            return TaskExecutionResult(
                success=False,
                result=result,
                error=f"Command exited with code {completed.returncode}",
            )

        return TaskExecutionResult(success=True, result=result)
