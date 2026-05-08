from __future__ import annotations

from typing import Any

from .agent_registry import AgentRegistry
from .local_tools import LocalTools
from .message_models import ExecutionResult, MessagePlan


class WorkerRunner:
    def __init__(self, tools: LocalTools, registry: AgentRegistry) -> None:
        self.tools = tools
        self.registry = registry

    def execute_plan(self, plan: MessagePlan, approval_granted: bool = False) -> list[ExecutionResult]:
        results: list[ExecutionResult] = []
        for task in plan.tasks:
            if task.tool.value == "fs_list":
                results.append(self.tools.fs_list(**task.args))
            elif task.tool.value == "fs_read":
                results.append(self.tools.fs_read(**task.args))
            elif task.tool.value == "fs_search":
                results.append(self.tools.fs_search(**task.args))
            elif task.tool.value == "git_status":
                results.append(self.tools.git_status(**task.args))
            elif task.tool.value == "run_python":
                results.append(self.tools.run_python(**task.args))
            elif task.tool.value == "run_shell":
                args: dict[str, Any] = {**task.args, "approval_granted": approval_granted}
                results.append(self.tools.run_shell(**args))
            elif task.tool.value == "agent_call":
                name = task.args.get("name", "")
                kwargs = {k: v for k, v in task.args.items() if k != "name"}
                results.append(self.registry.call(name, **kwargs))
            else:
                results.append(ExecutionResult(ok=False, tool=task.tool.value, summary="Unknown tool"))
        return results

    @staticmethod
    def summarize(results: list[ExecutionResult]) -> str:
        if not results:
            return "Задача завершилась без результатов."
        parts = []
        for idx, item in enumerate(results, start=1):
            state = "OK" if item.ok else "ERR"
            parts.append(f"{idx}. [{state}] {item.summary}")
        return "\n".join(parts)
