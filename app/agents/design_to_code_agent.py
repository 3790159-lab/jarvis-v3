from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from .base_agent import AgentResult, BaseAgent


class DesignToCodeAgent(BaseAgent):
    name = "design_to_code_agent"
    role = "claude_design_future_pipeline"

    def run(self, task: Dict[str, Any]) -> AgentResult:
        artifact_root = Path("jarvis_stage3_artifacts/full_creator/design_specs")
        artifact_root.mkdir(parents=True, exist_ok=True)

        product_name = task.get("product_name", "jarvis_product")
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in product_name.lower())

        spec_path = artifact_root / f"{safe_name}_design_spec.md"

        spec = f"""# Design Spec: {product_name}

## Goal
{task.get("goal", "Create a useful product/interface concept.")}

## Target user
{task.get("target_user", "Operator / business user / product user")}

## Screens
1. Landing / Overview
2. Main Dashboard
3. Task or Mission Detail
4. Settings / Integrations
5. Logs / Artifacts

## UI principles
- Clean layout
- Fast operator actions
- Visible status and risk
- Artifacts-first workflow
- Human approval for risky actions

## Claude Design future prompt
Create a polished UI/UX prototype for `{product_name}` with:
- dashboard
- task flow
- status cards
- artifact viewer
- approve/reject package flow
- modern SaaS style
"""

        spec_path.write_text(spec, encoding="utf-8")

        return AgentResult(
            status="ok",
            agent=self.name,
            message="Design specification artifact created",
            data={
                "product_name": product_name,
                "spec_path": str(spec_path),
                "future_pipeline": [
                    "Claude Design creates prototype",
                    "Claude Code converts prototype to frontend",
                    "QA validates",
                    "Night Mode improves safely",
                ],
            },
        )