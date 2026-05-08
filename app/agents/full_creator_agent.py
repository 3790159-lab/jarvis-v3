from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from .base_agent import AgentResult, BaseAgent
from .design_to_code_agent import DesignToCodeAgent
from .night_mode_strategist import NightModeStrategist
from .qa_agent import QAAgent


class FullCreatorAgent(BaseAgent):
    name = "full_creator_agent"
    role = "idea_to_design_to_code_to_launch"

    def run(self, task: Dict[str, Any]) -> AgentResult:
        artifact_root = Path("jarvis_stage3_artifacts/full_creator/packages")
        artifact_root.mkdir(parents=True, exist_ok=True)

        product_name = task.get("product_name", "Jarvis Mini SaaS")
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in product_name.lower())

        design = DesignToCodeAgent().run(task)
        night_strategy = NightModeStrategist().run(task)
        qa = QAAgent().run({"checks": ["design_spec", "code_package", "health", "artifact_index"]})

        package_path = artifact_root / f"{safe_name}_creator_package.md"

        package = f"""# Jarvis FULL CREATOR Package: {product_name}

## Mission
{task.get("goal", "Create a product from idea to launchable package.")}

## Pipeline
1. Idea validation
2. Claude Design / design spec
3. Claude Code implementation
4. QA checks
5. Package / PR
6. Night Mode safe improvement loop

## Current status
- Design artifact: {design.data.get("spec_path")}
- Night strategy: prepared
- QA: prepared

## Safe execution rule
Medium/high risk changes must be packaged first, not directly applied.

## Next implementation actions
- Generate frontend scaffold
- Generate backend endpoints
- Create artifact index
- Create smoke tests
- Create rollback plan
"""

        package_path.write_text(package, encoding="utf-8")

        return AgentResult(
            status="ok",
            agent=self.name,
            message="FULL CREATOR package prepared",
            data={
                "product_name": product_name,
                "package_path": str(package_path),
                "design": design.data,
                "night_strategy": night_strategy.data,
                "qa": qa.data,
            },
        )