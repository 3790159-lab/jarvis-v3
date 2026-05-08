from __future__ import annotations

from typing import Any, Dict, List


def get_claude_ecosystem_capabilities() -> Dict[str, Any]:
    return {
        "schema": "jarvis.claude_ecosystem.v1_1",
        "capabilities": {
            "claude_code": [
                "code_generation",
                "bug_fixing",
                "refactoring",
                "test_generation",
                "project_analysis",
                "execution_planning",
            ],
            "claude_design_future": [
                "ui_concepts",
                "operator_panel_design",
                "dashboard_design",
                "landing_page_design",
                "design_to_code_pipeline",
            ],
            "jarvis_integration": [
                "safe_hooks",
                "qa_agent",
                "architect_agent",
                "self_healing_health_snapshot",
                "night_mode_package_first_strategy",
            ],
        },
        "recommended_next_steps": [
            "Add hook calls into mission execution lifecycle.",
            "Add QAAgent into package/patch validation.",
            "Add Claude Design pipeline as artifact-first generator.",
            "Keep Night Mode in safe package/PR mode for medium-risk changes.",
        ],
    }


def list_recommended_agents() -> List[str]:
    return [
        "ArchitectAgent",
        "QAAgent",
        "BackendFixAgent",
        "N8nPipelineAgent",
        "MemoryAgent",
        "NightModeStrategist",
        "DesignToCodeAgent",
    ]