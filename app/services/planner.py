from typing import Any, Dict, List

from app.services.policy import get_policy


def _task(
    task_id: str,
    title: str,
    task_type: str,
    details: str,
    payload: Dict[str, Any] | None = None,
    depends_on: List[str] | None = None,
    priority: str = "normal",
) -> Dict[str, Any]:
    policy = get_policy()
    return {
        "task_id": task_id,
        "title": title,
        "type": task_type,
        "status": "queued",
        "details": details,
        "payload": payload or {},
        "depends_on": depends_on or [],
        "priority": priority,
        "max_retries": int(policy.get("max_retries", 2)),
    }


def build_tasks_from_objective(objective: str) -> List[Dict[str, Any]]:
    text = (objective or "").strip()
    low = text.lower()

    tasks: List[Dict[str, Any]] = []
    tasks.append(_task("interpret_request", "Interpret request", "analysis", "Understand objective and expected outcome.", priority="high"))

    if any(x in low for x in ["telegram", "bot", "телеграм", "бот"]):
        tasks.append(_task("telegram_layer", "Prepare Telegram layer", "integration", "Review Telegram routing and interaction layer.", depends_on=["interpret_request"], priority="high"))

    if any(x in low for x in ["api", "backend", "fastapi", "endpoint", "бэкенд"]):
        tasks.append(_task("backend_layer", "Prepare backend layer", "backend", "Validate endpoints, response format and stability.", depends_on=["interpret_request"], priority="high"))

    if any(x in low for x in ["ollama", "llm", "ai", "ии", "модель"]):
        tasks.append(_task("llm_layer", "Prepare LLM layer", "llm", "Enable LLM routing, timeout protection and fallback.", depends_on=["backend_layer"], priority="normal"))

    if any(x in low for x in ["plan", "roadmap", "этап", "план", "развит"]):
        tasks.append(_task("planning_layer", "Build plan", "planning", "Create phased roadmap with stability-first sequencing.", depends_on=["interpret_request"], priority="normal"))

    if any(x in low for x in ["file", "artifact", "файл", "артефакт", "write"]):
        tasks.append(_task(
            "artifact_note",
            "Write artifact note",
            "file_write",
            "Write a controlled output artifact.",
            payload={"filename": "mission_note.txt", "content": f"Objective: {text}\n"},
            depends_on=["interpret_request"],
            priority="low",
        ))

    tasks.append(_task("stability_review", "Run stability review", "review", "Check fallback behavior, response speed and degradation strategy.", depends_on=["interpret_request"], priority="normal"))
    return tasks


def build_strategy_summary(objective: str) -> str:
    return (
        "Supervisor prepared a phased mission: interpret request, resolve dependencies, "
        "prioritize tasks, route execution safely, and preserve stability through fallback and policy control."
    )


def infer_priority(objective: str) -> str:
    low = (objective or "").lower()
    if any(x in low for x in ["urgent", "срочно", "critical", "критично", "немедленно"]):
        return "high"
    if any(x in low for x in ["later", "потом", "низкий", "low priority"]):
        return "low"
    return "normal"


def create_plan(objective: str) -> Dict[str, Any]:
    return {
        "objective": (objective or "").strip(),
        "strategy_summary": build_strategy_summary(objective),
        "priority": infer_priority(objective),
        "tasks": build_tasks_from_objective(objective),
    }

def plan_task(task, *args, **kwargs):
    """
    Compatibility fallback for routes that import plan_task.
    """
    objective = ""
    constraints = []

    if isinstance(task, dict):
        objective = str(task.get("objective") or task.get("task") or task.get("message") or "")
        constraints = task.get("constraints") or []
    else:
        objective = str(task)

    objective = objective.strip() or "generic task"

    return {
        "status": "compat_fallback",
        "objective": objective,
        "summary": f"Fallback plan for: {objective}",
        "steps": [
            {"id": "analyze", "title": "Analyze request", "status": "ready"},
            {"id": "plan", "title": "Build execution plan", "status": "ready"},
            {"id": "execute", "title": "Execute selected tools", "status": "ready"},
            {"id": "finalize", "title": "Finalize result", "status": "ready"},
        ],
        "constraints": constraints,
        "tool_hints": ["tool_registry", "tool_executor", "agent_control_plane"],
    }
