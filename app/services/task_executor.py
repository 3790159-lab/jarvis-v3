import time
from typing import Any, Dict

from app.services.safe_executor import safe_run_shell, safe_write_text_file
from app.services.tool_registry import get_tool_spec


def _artifact_text(title: str, details: str, payload: Dict[str, Any] | None = None) -> str:
    payload = payload or {}
    lines = [f"Title: {title}", f"Details: {details}"]
    if payload:
        lines += ["", "Payload:"]
        for k, v in payload.items():
            lines.append(f"- {k}: {v}")
    return "\n".join(lines) + "\n"


def execute_task(task: Dict[str, Any]) -> Dict[str, Any]:
    mission_id = task.get("mission_id", "unknown_mission")
    task_id = task.get("task_id", "unknown_task")
    task_type = task.get("type", "generic")
    title = task.get("title", "")
    details = task.get("details", "")
    payload = task.get("payload", {}) or {}
    spec = get_tool_spec(task_type)

    started = time.time()

    try:
        if task_type == "analysis":
            output = {"message": "Request interpreted successfully"}
            message = "Analysis task completed"

        elif task_type in {"integration", "backend", "llm", "planning"}:
            artifact = safe_write_text_file(f"{mission_id}_{task_id}.txt", _artifact_text(title, details, payload))
            output = {"message": f"{task_type} artifact created", **artifact}
            message = f"{task_type} task completed"

        elif task_type == "review":
            output = {"message": "Stability review simulated successfully"}
            message = "Review task completed"

        elif task_type == "file_write":
            filename = str(payload.get("filename", f"{mission_id}_{task_id}.txt"))
            content = str(payload.get("content", _artifact_text(title, details, payload)))
            artifact = safe_write_text_file(filename, content)
            output = {"message": "Controlled file written", **artifact}
            message = "File write task completed"

        elif task_type == "shell":
            command = str(payload.get("command", "")).strip()
            result = safe_run_shell(command)
            output = {"message": "Shell command executed", **result}
            message = "Shell task completed"

        else:
            artifact = safe_write_text_file(f"{mission_id}_{task_id}.txt", _artifact_text(title, details, payload))
            output = {"message": "Generic artifact created", **artifact}
            message = "Generic task completed"

        elapsed_ms = int((time.time() - started) * 1000)
        return {
            "task_id": task_id,
            "title": title,
            "type": task_type,
            "tool_spec": spec,
            "status": "completed",
            "attempt_count": int(task.get("attempt_count", 1)),
            "worker_id": task.get("worker_id", ""),
            "message": message,
            "output": output,
            "elapsed_ms": elapsed_ms,
        }

    except Exception as e:
        elapsed_ms = int((time.time() - started) * 1000)
        return {
            "task_id": task_id,
            "title": title,
            "type": task_type,
            "tool_spec": spec,
            "status": "failed",
            "attempt_count": int(task.get("attempt_count", 1)),
            "worker_id": task.get("worker_id", ""),
            "message": f"{type(e).__name__}: {e}",
            "output": {},
            "elapsed_ms": elapsed_ms,
        }