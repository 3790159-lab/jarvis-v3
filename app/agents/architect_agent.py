from __future__ import annotations

from typing import Any, Dict

from .base_agent import AgentResult, BaseAgent


class ArchitectAgent(BaseAgent):
    name = "architect_agent"
    role = "system_design"

    def run(self, task: Dict[str, Any]) -> AgentResult:
        return AgentResult(
            status="ok",
            agent=self.name,
            message="Architecture review prepared",
            data={
                "principles": [
                    "Do not overwrite working modules without backup.",
                    "Prefer additive upgrades.",
                    "Route risky changes through package/patch/PR mode.",
                    "Every execution must produce artifacts and checks."
                ]
            },
        )