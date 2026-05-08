from pathlib import Path
from typing import Any, Dict

from app.services.context_manager import ensure_context_compressed, get_resume_bundle
from app.services.llm_router import route_message
from app.services.mission_memory import append_memory, get_memory, memory_stats
from app.services.mission_store import (
    create_mission,
    get_mission,
    list_missions,
    mission_stats,
    update_mission_context,
    update_mission_status,
)
from app.services.planner import create_plan
from app.services.policy import get_policy
from app.services.task_queue import (
    cancel_mission_queue_items,
    enqueue_tasks,
    mission_queue_items,
    queue_stats,
    retry_failed_tasks,
)
from app.services.tool_registry import get_tool_registry


BASE_DIR = Path(__file__).resolve().parents[2]
LOGS_DIR = BASE_DIR / "artifacts" / "logs"
OUTPUT_DIR = BASE_DIR / "artifacts" / "output"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _mission_status_text(mission_id: str) -> str:
    mission = get_mission(mission_id)
    if not mission:
        return f"Миссия не найдена: {mission_id}"

    queue_items = mission_queue_items(mission_id)
    queued = sum(1 for i in queue_items if i.get("status") == "queued")
    running = sum(1 for i in queue_items if i.get("status") == "running")
    failed = sum(1 for i in queue_items if i.get("status") == "failed")
    completed = sum(1 for i in queue_items if i.get("status") == "completed")

    return (
        f"mission_id: {mission.get('mission_id')}\n"
        f"goal_id: {mission.get('goal_id')}\n"
        f"status: {mission.get('status')}\n"
        f"priority: {mission.get('context', {}).get('priority', 'normal')}\n"
        f"objective: {mission.get('objective')}\n"
        f"tasks_total: {len(mission.get('tasks', []))}\n"
        f"queue: queued={queued}, running={running}, completed={completed}, failed={failed}"
    )


def _mission_logs_text(mission_id: str) -> str:
    mission = get_mission(mission_id)
    if not mission:
        return f"Миссия не найдена: {mission_id}"

    lines = [f"logs for {mission_id}:"]
    for item in mission.get("task_results", [])[-20:]:
        lines.append(
            f"- task_id={item.get('task_id')} status={item.get('status')} "
            f"attempt={item.get('attempt_count')} message={item.get('message')}"
        )
    if len(lines) == 1:
        lines.append("Пока нет task_results.")
    return "\n".join(lines)


def _command_response(text: str) -> Dict[str, Any] | None:
    t = (text or "").strip()
    tl = t.lower()

    if tl == "/missions":
        items = list_missions(limit=10)
        if not items:
            reply = "Миссий пока нет."
        else:
            lines = ["Последние миссии:"]
            for item in items[:10]:
                lines.append(f"- {item.get('mission_id')} | {item.get('status')} | {item.get('objective')}")
            reply = "\n".join(lines)
        return {"reply": reply, "mode": "command_local", "source": "command_handler", "confidence": 0.99, "route": "mission"}

    if tl.startswith("/mission "):
        mission_id = t.split(" ", 1)[1].strip()
        return {"reply": _mission_status_text(mission_id), "mode": "command_local", "source": "command_handler", "confidence": 0.99, "route": "mission"}

    if tl.startswith("/status "):
        mission_id = t.split(" ", 1)[1].strip()
        return {"reply": _mission_status_text(mission_id), "mode": "command_local", "source": "command_handler", "confidence": 0.99, "route": "mission"}

    if tl.startswith("/logs "):
        mission_id = t.split(" ", 1)[1].strip()
        return {"reply": _mission_logs_text(mission_id), "mode": "command_local", "source": "command_handler", "confidence": 0.99, "route": "mission"}

    if tl.startswith("/retry "):
        mission_id = t.split(" ", 1)[1].strip()
        result = retry_failed_tasks(mission_id)
        append_memory(mission_id, "retry_requested", result)
        update_mission_status(mission_id, "planned", "Retry requested for failed tasks.")
        return {
            "reply": f"Retry requested.\nmission_id: {mission_id}\nrequeued_failed: {result.get('requeued_failed', 0)}",
            "mode": "command_local",
            "source": "command_handler",
            "confidence": 0.99,
            "route": "mission",
        }

    if tl.startswith("/cancel "):
        mission_id = t.split(" ", 1)[1].strip()
        result = cancel_mission_queue_items(mission_id)
        append_memory(mission_id, "cancel_requested", result)
        update_mission_status(mission_id, "cancelled", "Mission cancellation requested.")
        return {
            "reply": (
                f"Cancel requested.\nmission_id: {mission_id}\n"
                f"cancelled_queued: {result.get('cancelled_queued', 0)}\n"
                f"cancel_requested_running: {result.get('cancel_requested_running', 0)}"
            ),
            "mode": "command_local",
            "source": "command_handler",
            "confidence": 0.99,
            "route": "mission",
        }

    if tl.startswith("/run "):
        mission_id = t.split(" ", 1)[1].strip()
        result = retry_failed_tasks(mission_id)
        append_memory(mission_id, "run_requested", result)
        update_mission_status(mission_id, "planned", "Run requested.")
        return {
            "reply": f"Run requested.\nmission_id: {mission_id}\nrequeued_failed: {result.get('requeued_failed', 0)}",
            "mode": "command_local",
            "source": "command_handler",
            "confidence": 0.99,
            "route": "mission",
        }

    if tl.startswith("/memory "):
        mission_id = t.split(" ", 1)[1].strip()
        mem = get_memory(mission_id)
        lines = [f"memory for {mission_id}:"]
        for event in mem.get("events", [])[-10:]:
            lines.append(f"- {event.get('kind')} | {event.get('content')}")
        if mem.get("summaries"):
            lines.append("summaries:")
            for item in mem.get("summaries", [])[-5:]:
                lines.append(f"- {item.get('summary')}")
        return {"reply": "\n".join(lines), "mode": "command_local", "source": "command_handler", "confidence": 0.99, "route": "mission"}

    if tl.startswith("/context "):
        mission_id = t.split(" ", 1)[1].strip()
        bundle = get_resume_bundle(mission_id)
        reply = (
            f"resume context for {mission_id}:\n"
            f"{bundle.get('resume_context', {}).get('resume_text', '') or 'Контекст пока пуст.'}"
        )
        return {"reply": reply, "mode": "command_local", "source": "command_handler", "confidence": 0.99, "route": "mission"}

    if tl.startswith("/compress "):
        mission_id = t.split(" ", 1)[1].strip()
        result = ensure_context_compressed(mission_id)
        return {
            "reply": f"compression result for {mission_id}:\n{result}",
            "mode": "command_local",
            "source": "command_handler",
            "confidence": 0.99,
            "route": "mission",
        }

    if tl == "/diag":
        reply = (
            f"policy: {get_policy().get('profile')}\n"
            f"mission_stats: {mission_stats()}\n"
            f"queue_stats: {queue_stats()}\n"
            f"memory_stats: {memory_stats()}"
        )
        return {"reply": reply, "mode": "command_local", "source": "command_handler", "confidence": 0.99, "route": "status"}

    if tl == "/tools":
        tools = get_tool_registry()
        lines = ["Доступные task tools:"]
        for key, value in tools.items():
            lines.append(f"- {key}: {value.get('executor')} | safe={value.get('safe')} | {value.get('description')}")
        return {"reply": "\n".join(lines), "mode": "command_local", "source": "command_handler", "confidence": 0.99, "route": "technical"}

    return None


def handle_supervisor_message(text: str, source: str = "api") -> Dict[str, Any]:
    command = _command_response(text or "")
    if command is not None:
        return command

    result = route_message(text or "")
    route = result.get("route", "chat")

    if route in {"mission", "task"}:
        plan = create_plan(text or "")
        mission = create_mission(
            objective=plan["objective"],
            route=route,
            strategy_summary=plan["strategy_summary"],
            tasks=plan["tasks"],
            source=source,
            mode=result.get("mode", "smart_local"),
        )

        update_mission_context(mission["mission_id"], "priority", plan.get("priority", "normal"))
        append_memory(mission["mission_id"], "mission_created", {"objective": plan["objective"], "priority": plan.get("priority", "normal")})
        enqueue_tasks(mission["mission_id"], mission["tasks"])
        update_mission_status(mission["mission_id"], "planned", "Mission planned and long-autonomy tasks queued.")
        refreshed_mission = get_mission(mission["mission_id"]) or mission

        reply = (
            f"{result.get('reply', '').strip()}\n\n"
            f"Создана и поставлена в очередь long-autonomy миссия.\n"
            f"mission_id: {refreshed_mission['mission_id']}\n"
            f"goal_id: {refreshed_mission['goal_id']}\n"
            f"priority: {refreshed_mission.get('context', {}).get('priority', 'normal')}\n"
            f"tasks: {len(refreshed_mission.get('tasks', []))}\n"
            f"status: {refreshed_mission.get('status')}"
        ).strip()

        result["reply"] = reply
        result["mission"] = refreshed_mission

    return result


def debug_routes_snapshot(sample_text: str) -> Dict[str, Any]:
    result = route_message(sample_text or "")
    return {
        "input": sample_text,
        "result": result,
        "policy": get_policy(),
        "mission_stats": mission_stats(),
        "queue_stats": queue_stats(),
        "memory_stats": memory_stats(),
        "tools": get_tool_registry(),
    }


def list_all_missions(limit: int = 50):
    return list_missions(limit=limit)


def read_mission(mission_id: str):
    return get_mission(mission_id)