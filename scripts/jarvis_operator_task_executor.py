from __future__ import annotations

import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_operator_task_center import JarvisOperatorTaskCenter
from app.services.jarvis_unified_autonomous_loop import JarvisUnifiedAutonomousLoop


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def execute_task(center: JarvisOperatorTaskCenter, task: dict) -> dict:
    task_id = task["task_id"]
    objective = task.get("objective") or task.get("title") or "Operator task"

    center.update_task(
        task_id,
        status="running",
        stage="planning",
        progress=10,
        next_action="Формирую безопасный план выполнения.",
        notes=(task.get("notes") or []) + [f"Started at {utc_now_iso()}"],
    )

    loop = JarvisUnifiedAutonomousLoop(project_root=PROJECT_ROOT)

    center.update_task(
        task_id,
        stage="unified_loop",
        progress=35,
        next_action="Запускаю unified autonomous loop для задачи.",
    )

    goal = "Execute operator task safely"
    if "n8n" in objective.lower():
        goal = "Inspect and improve n8n pipeline readiness safely"

    result = loop.run(
        goal=goal,
        task=objective,
        priority=task.get("priority", "normal"),
    )

    center.update_task(
        task_id,
        stage="validation",
        progress=80,
        next_action="Проверяю результат и сохраняю отчёт.",
    )

    execution = {
        "task_id": task_id,
        "objective": objective,
        "loop_run_id": result.loop_run_id,
        "loop_status": result.status,
        "recommended_action": result.recommended_action,
        "apply_lane_state": result.apply_lane_state,
        "mutation_outcome": result.mutation_outcome,
        "blocked_reasons": result.blocked_reasons,
        "next_best_action": result.next_best_action,
        "operator_message": result.operator_message,
        "finished_at": utc_now_iso(),
    }

    out = PROJECT_ROOT / "jarvis_stage3_artifacts" / "operator_task_center" / "executions" / f"{task_id}.json"
    write_json(out, execution)

    final_status = "completed" if result.status in {"completed", "degraded"} else "failed"
    final_progress = 100 if final_status == "completed" else 90

    center.update_task(
        task_id,
        status=final_status,
        stage="finished",
        progress=final_progress,
        next_action=result.next_best_action or "Задача завершена.",
        notes=(task.get("notes") or []) + [
            f"Finished at {utc_now_iso()}",
            f"Loop status: {result.status}",
            f"Loop run id: {result.loop_run_id}",
        ],
    )

    return execution


def main() -> int:
    center = JarvisOperatorTaskCenter(PROJECT_ROOT)
    print("Jarvis Operator Task Executor started.")

    while True:
        try:
            tasks = center.list_tasks()
            queued = [t for t in tasks if t.get("status") == "queued"]

            if not queued:
                time.sleep(5)
                continue

            task = queued[-1]  # older first because list_tasks returns newest first
            print(f"Executing task: {task.get('task_id')} :: {task.get('title')}")
            execution = execute_task(center, task)
            print(json.dumps(execution, ensure_ascii=False, indent=2))

        except KeyboardInterrupt:
            print("Executor stopped.")
            return 0
        except Exception as exc:
            err = {
                "error": str(exc),
                "traceback": traceback.format_exc(),
                "created_at": utc_now_iso(),
            }
            err_path = PROJECT_ROOT / "jarvis_stage3_artifacts" / "operator_task_center" / "errors" / f"executor_error_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            write_json(err_path, err)
            print(f"Executor error: {exc}")
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())