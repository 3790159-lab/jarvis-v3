from __future__ import annotations


def plan_task(payload: dict) -> list[dict]:
    objective = payload.get("objective", "")
    depth = int(payload.get("depth", 0))

    if depth > 3:
        raise ValueError("Max planning depth exceeded")

    tasks: list[dict] = []

    if "file" in objective.lower():
        target_path = "./artifacts/auto_plan.txt"
        planned_content = f"Planned execution for: {objective}"

        tasks.append({
            "task_type": "write_file",
            "payload": {
                "path": target_path,
                "content": planned_content,
                "depth": depth + 1,
            },
            "assigned_agent": "executor_agent",
        })

        tasks.append({
            "task_type": "critic_check",
            "payload": {
                "mode": "file_exists",
                "path": target_path,
                "depth": depth + 1,
            },
            "assigned_agent": "critic_agent",
        })

    else:
        tasks.append({
            "task_type": "echo",
            "payload": {
                "message": f"Planner processed: {objective}",
                "depth": depth + 1,
            },
            "assigned_agent": "executor_agent",
        })

    return tasks
