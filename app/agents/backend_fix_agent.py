from __future__ import annotations

from typing import Any, Dict

from .base_agent import AgentResult, BaseAgent
from app.services.self_healing import check_backend, recommend_recovery


class BackendFixAgent(BaseAgent):
    name = "backend_fix_agent"
    role = "backend_self_healing"

    def run(self, task: Dict[str, Any]) -> AgentResult:
        base_url = task.get("base_url", "http://127.0.0.1:8015")
        snapshot = check_backend(base_url=base_url)
        recovery = recommend_recovery(snapshot)

        status = "ok" if snapshot.ok else "needs_recovery"

        return AgentResult(
            status=status,
            agent=self.name,
            message="Backend health analyzed",
            data={
                "backend_health": snapshot.to_dict(),
                "recovery": recovery,
                "safe_actions": [
                    "restart_backend_then_recheck",
                    "collect_compile_errors",
                    "read_backend_err_log",
                    "generate_patch_package_before_apply",
                ],
            },
        )