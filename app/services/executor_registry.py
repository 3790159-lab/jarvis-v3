from __future__ import annotations
import app.core.bootstrap_utf8  # noqa: F401

from datetime import datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ARTIFACTS_DIR = BASE_DIR / "artifacts"
OUTPUT_DIR = ARTIFACTS_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def utc_now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


class ExecutorRegistry:
    def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        task_type = task.get("type", "unknown")
        params = task.get("params", {}) or {}
        objective = str(params.get("objective", "")).strip()

        if task_type == "file_write_stub":
            out_file = OUTPUT_DIR / "stage1_file_write_stub.txt"
            out_file.write_text(
                f"Stub file execution for objective:\n{objective}\n",
                encoding="utf-8",
            )
            return {
                "task_id": task.get("task_id"),
                "status": "completed",
                "message": "Stub file write completed",
                "output": {
                    "path": str(out_file),
                    "objective": objective,
                    "executed_at": utc_now_iso(),
                },
            }

        if task_type == "repair_stub":
            return {
                "task_id": task.get("task_id"),
                "status": "completed",
                "message": "Repair stub completed",
                "output": {
                    "objective": objective,
                    "executed_at": utc_now_iso(),
                },
            }

        if task_type == "analysis_stub":
            return {
                "task_id": task.get("task_id"),
                "status": "completed",
                "message": "Analysis stub completed",
                "output": {
                    "objective": objective,
                    "executed_at": utc_now_iso(),
                },
            }

        return {
            "task_id": task.get("task_id"),
            "status": "failed",
            "message": f"Unsupported task type: {task_type}",
            "output": {
                "executed_at": utc_now_iso(),
            },
        }
