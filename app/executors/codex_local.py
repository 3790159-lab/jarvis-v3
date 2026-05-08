from __future__ import annotations

import os
import subprocess
from pathlib import Path

from app.executors.base import BaseExecutor
from app.models import TaskExecutionResult
from app.settings import settings


class CodexLocalExecutor(BaseExecutor):
    """
    Thin adapter around the local Codex CLI.

    Expected payload:
    {
      "prompt": "Audit this repository",
      "timeout_seconds": 6000,
      "extra_args": ["--some-flag"]
    }
    """

    def run(self, task: dict) -> TaskExecutionResult:
        payload = task.get("payload", {})
        prompt = payload.get("prompt")
        if not prompt:
            return TaskExecutionResult(success=False, error="Missing payload.prompt for codex_local task.")

        working_directory = task.get("working_directory") or os.getcwd()
        cwd = Path(working_directory).resolve()
        if not cwd.exists():
            return TaskExecutionResult(success=False, error=f"Working directory does not exist: {cwd}")

        command = [settings.codex_command]
        model = payload.get("model") or settings.codex_default_model
        if model:
            command.extend(["--model", model])

        command.extend(payload.get("extra_args", []))
        command.append(prompt)

        try:
            completed = subprocess.run(
                command,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=payload.get("timeout_seconds", 1800),
            )
        except FileNotFoundError:
            return TaskExecutionResult(
                success=False,
                error=(
                    f"Codex command not found: {settings.codex_command}. "
                    "Install Codex CLI and ensure it is available in PATH."
                ),
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
                error=f"Codex CLI exited with code {completed.returncode}",
            )

        return TaskExecutionResult(success=True, result=result)
