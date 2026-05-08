from __future__ import annotations

from typing import Any, Dict, List


ROLE_KEYWORDS = {
    "planner_core": {
        "plan", "planning", "strategy", "roadmap", "outline", "breakdown", "analyze", "analysis"
    },
    "code_core": {
        "code", "refactor", "implement", "patch", "fix", "bug", "module", "api", "endpoint", "script", "test"
    },
    "qa_core": {
        "qa", "validate", "verification", "verify", "check", "audit", "inspect", "review"
    },
    "executor_core": {
        "execute", "run", "launch", "start", "call", "request", "deploy", "powershell", "shell", "http"
    },
}


def normalize_text(value: str) -> str:
    return (value or "").strip().lower()


def choose_agent(
    objective: str,
    requested_tools: List[str] | None = None,
    task_type: str | None = None,
    input_payload: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    requested_tools = requested_tools or []
    input_payload = input_payload or {}
    text = " ".join([
        normalize_text(task_type or ""),
        normalize_text(objective or ""),
        normalize_text(str(input_payload))
    ])

    reasons: List[str] = []

    if task_type in {"plan", "planning", "strategy"}:
        reasons.append("task_type matched planner role")
        return {
            "agent_id": "planner_core",
            "role": "planner",
            "reason": reasons
        }

    if task_type in {"qa", "validate", "verification", "review"}:
        reasons.append("task_type matched qa role")
        return {
            "agent_id": "qa_core",
            "role": "qa",
            "reason": reasons
        }

    if task_type in {"code", "implement", "patch", "refactor"}:
        reasons.append("task_type matched code role")
        return {
            "agent_id": "code_core",
            "role": "code",
            "reason": reasons
        }

    risky_exec_tools = {"shell", "http", "filesystem", "python"}
    if set(requested_tools).intersection(risky_exec_tools):
        code_bias_words = {"code", "refactor", "module", "script", "test", "patch", "endpoint", "api"}
        if any(word in text for word in code_bias_words):
            reasons.append("requested tools present but objective indicates code-oriented work")
            return {
                "agent_id": "code_core",
                "role": "code",
                "reason": reasons
            }

        reasons.append("requested tools require executor capabilities")
        return {
            "agent_id": "executor_core",
            "role": "executor",
            "reason": reasons
        }

    for agent_id, keywords in ROLE_KEYWORDS.items():
        if any(word in text for word in keywords):
            reasons.append(f"keyword match for {agent_id}")
            return {
                "agent_id": agent_id,
                "role": agent_id.replace("_core", "").replace("planner", "planner").replace("executor", "executor").replace("qa", "qa").replace("code", "code"),
                "reason": reasons
            }

    reasons.append("default route to planner for unknown/general objective")
    return {
        "agent_id": "planner_core",
        "role": "planner",
        "reason": reasons
    }


def build_handoff_path(agent_id: str) -> List[str]:
    if agent_id == "planner_core":
        return ["planner_core", "qa_core"]

    if agent_id == "code_core":
        return ["planner_core", "code_core", "qa_core"]

    if agent_id == "executor_core":
        return ["planner_core", "executor_core", "qa_core"]

    if agent_id == "qa_core":
        return ["qa_core"]

    return ["planner_core", "qa_core"]
