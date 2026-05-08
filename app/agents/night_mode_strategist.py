from __future__ import annotations

from typing import Any, Dict

from .base_agent import AgentResult, BaseAgent


class NightModeStrategist(BaseAgent):
    name = "night_mode_strategist"
    role = "safe_autonomous_improvement"

    def run(self, task: Dict[str, Any]) -> AgentResult:
        return AgentResult(
            status="ok",
            agent=self.name,
            message="Night Mode strategy prepared",
            data={
                "policy": "safe_package_first",
                "auto_apply_allowed": [
                    "documentation",
                    "artifact_index",
                    "diagnostic_scripts",
                    "non-invasive health checks",
                    "additive tests",
                    "logs and observability improvements",
                ],
                "approval_or_package_required": [
                    "main router rewrites",
                    "database migrations",
                    "credential changes",
                    "deleting files",
                    "changing core mission execution",
                    "external tool execution adapters",
                ],
                "required_after_each_iteration": [
                    "compile_check",
                    "backend_health_check",
                    "artifact_log",
                    "diff_summary",
                    "rollback_plan",
                    "operator_summary",
                ],
            },
        )