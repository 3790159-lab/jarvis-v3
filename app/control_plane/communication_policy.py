from __future__ import annotations

import json
from pathlib import Path


class CommunicationPolicyStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.data = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.data = {
                "risk_budgets": {
                    "low": 0,
                    "normal": 1,
                    "high": 2,
                    "critical": 3,
                },
                "task_overrides": {
                    "planning": 0,
                    "research": 1,
                    "validation": 1,
                    "codegen": 2,
                    "integration": 0,
                    "memory_write": 0,
                    "connector_execution": 0,
                    "service_probe": 0,
                },
                "allowed_support": {
                    "plan": ["analyze", "context_bundle", "validate"],
                    "analyze": ["context_bundle", "validate"],
                    "codegen": ["analyze", "context_bundle", "validate"],
                    "validate": ["context_bundle"],
                    "workflow_run": ["validate"],
                    "memory_write": [],
                    "context_bundle": [],
                },
            }
            self.save()
            return
        self.data = json.loads(self.path.read_text(encoding="utf-8"))

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def max_consults(self, task_type: str, risk_level: str, strategy_bonus: int = 0) -> int:
        task_override = self.data.get("task_overrides", {}).get(task_type)
        if task_override is not None:
            base = int(task_override)
        else:
            base = int(self.data.get("risk_budgets", {}).get(risk_level, 1))
        return max(0, min(4, base + int(strategy_bonus or 0)))

    def allowed_helpers(self, capability: str) -> list[str]:
        return list(self.data.get("allowed_support", {}).get(capability, []))

    def dump(self) -> dict:
        self.load()
        return dict(self.data)