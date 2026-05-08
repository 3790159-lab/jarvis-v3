from __future__ import annotations

from typing import Any, Dict

try:
    from app.services.jarvis_internet_tools import run_internet_tool
except Exception:
    run_internet_tool = None


def think_and_enhance(task: str, quality_target: str = "high") -> Dict[str, Any]:
    decision = {
        "ok": True,
        "use_internet": False,
        "reason": "",
        "enhanced_task": task,
        "research": None,
    }

    text = (task or "").lower()

    triggers = [
        "best",
        "compare",
        "latest",
        "current",
        "2026",
        "api",
        "tools",
        "services",
        "pipeline",
        "architecture",
        "найди",
        "поищи",
        "сравни",
        "актуальн",
        "лучший",
        "сервисы",
        "пайплайн",
    ]

    if any(t in text for t in triggers):
        decision["use_internet"] = True
        decision["reason"] = "Task needs current internet research."

    if quality_target in {"research", "ultra", "best"}:
        decision["use_internet"] = True
        decision["reason"] = "Quality target requires research."

    if len(task or "") > 160:
        decision["use_internet"] = True
        decision["reason"] = "Complex long task."

    if decision["use_internet"] and run_internet_tool:
        research = run_internet_tool(
            "internet.engineer_brief",
            {"task": task}
        )
        decision["research"] = research

        answer = (
            research.get("research", {}).get("answer")
            or research.get("answer")
            or ""
        )

        if research.get("ok") and answer:
            decision["enhanced_task"] = (
                task
                + "\n\nJarvis internet research summary:\n"
                + answer[:2500]
            )

    return decision