from __future__ import annotations

from typing import Any, Dict, List
import os

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/execution-planner", tags=["execution_planner"])


class PlanRequest(BaseModel):
    goal: str
    context: Dict[str, Any] = Field(default_factory=dict)


def _tool_plan_for_goal(goal: str) -> List[dict]:
    g = _normalize_text(goal).lower()

    if any(word in g for word in ["прочитай", "read file", "open file", "файл"]):
        return [
            {"step_id": "read_file", "tool_name": "file_read", "reason": "Goal mentions reading a file."}
        ]

    if any(word in g for word in ["запиши", "сохрани", "write file", "create file", "создай файл"]):
        return [
            {"step_id": "write_file", "tool_name": "file_write", "reason": "Goal mentions creating or writing a file."}
        ]

    if any(word in g for word in ["url", "http", "api", "сайт", "webhook", "endpoint"]):
        return [
            {"step_id": "http_call", "tool_name": "http", "reason": "Goal mentions URL/API/HTTP access."}
        ]

    if any(word in g for word in ["python", "код", "script", "скрипт", "вычисли"]):
        return [
            {"step_id": "run_python", "tool_name": "python", "reason": "Goal suggests running Python logic."}
        ]

    if any(word in g for word in ["cmd", "shell", "powershell", "команда", "терминал"]):
        return [
            {"step_id": "run_shell", "tool_name": "shell", "reason": "Goal mentions command execution."}
        ]

    return [
        {"step_id": "respond_only", "tool_name": "respond", "reason": "No strong tool trigger detected; keep it conversational."}
    ]


@router.post("/plan")
def execution_plan(request: PlanRequest):
    steps = _tool_plan_for_goal(request.goal)
    return {
        "status": "ok",
        "goal": request.goal,
        "steps": steps,
        "step_count": len(steps),
    }


def _normalize_text(value: str) -> str:
    text = str(value or "")
    return text.strip()
