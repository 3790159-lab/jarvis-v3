from __future__ import annotations

from typing import Any, Dict, List

from .base_agent import AgentResult, BaseAgent


class QAAgent(BaseAgent):
    name = "qa_agent"
    role = "compile_test_review"

    def run(self, task: Dict[str, Any]) -> AgentResult:
        checks: List[str] = task.get("checks") or ["compile", "health", "artifacts"]
        return AgentResult(
            status="ok",
            agent=self.name,
            message="QA checklist prepared",
            data={
                "checks": checks,
                "recommendation": "Run compile checks, endpoint health checks, and artifact validation before merge."
            },
        )