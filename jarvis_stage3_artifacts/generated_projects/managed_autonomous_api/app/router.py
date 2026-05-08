from __future__ import annotations


def route_task(task_type: str) -> str:
    if task_type in ["echo", "write_file", "read_file", "list_dir", "run_python_tests"]:
        return "executor_agent"

    if task_type == "run_shell_command":
        return "shell_agent"

    if task_type in ["plan", "route"]:
        return "planner_agent"

    if task_type in ["critic_check"]:
        return "critic_agent"

    return "planner_agent"
