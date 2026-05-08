from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from app.services.jarvis_thinking_layer import think_and_enhance
from app.services.jarvis_internet_tools import run_internet_tool

ROOT = Path.cwd()
ART_DIR = ROOT / "jarvis_stage3_artifacts" / "ai_engineer"
ART_DIR.mkdir(parents=True, exist_ok=True)


def _save(name: str, data: Dict[str, Any]) -> str:
    path = ART_DIR / f"{name}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def ai_engineer_review(task: str, mode: str = "safe_plan") -> Dict[str, Any]:
    thinking = think_and_enhance(task, "research")

    research = run_internet_tool(
        "internet.engineer_brief",
        {"task": task}
    )

    plan = [
        "1. Clarify task and success criteria",
        "2. Research current tools/services/API options",
        "3. Compare providers and risks",
        "4. Design safe integration adapter",
        "5. Add tests, logs, fallback, and rollback",
        "6. Run smoke test",
        "7. Prepare operator-ready implementation block"
    ]

    recommendation = {
        "mode": mode,
        "safe_to_auto_apply": False,
        "reason": "This engineer layer produces plans and patches, but does not auto-modify critical systems without operator approval.",
        "next_best_action": "Use this report to choose provider and generate adapter code."
    }

    result = {
        "ok": True,
        "tool": "jarvis.ai_engineer_review",
        "task": task,
        "mode": mode,
        "thinking": thinking,
        "research": research,
        "plan": plan,
        "recommendation": recommendation,
    }

    result["artifact_path"] = _save("ai_engineer_review", result)
    return result


def recommend_next_provider(goal: str) -> Dict[str, Any]:
    return ai_engineer_review(
        "Recommend the best next AI provider/API for Jarvis for this goal: " + goal,
        mode="provider_selection"
    )