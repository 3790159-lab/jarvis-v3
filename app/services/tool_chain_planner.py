from __future__ import annotations

from typing import Any, Dict, List

from app.services.tool_router import select_tool


def _step_text(objective: str, step: Dict[str, Any]) -> str:
    return f"{objective} {step.get('title', '')} {step.get('description', '')}".strip().lower()


def _manual_chain(metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    actions = metadata.get("tool_chain")
    if not isinstance(actions, list):
        return []
    out = []
    for item in actions:
        if not isinstance(item, dict):
            continue
        tool = item.get("tool")
        payload = item.get("payload") or {}
        if tool:
            out.append({"tool": tool, "payload": payload})
    return out


def _single_tool_plan(objective: str, step: Dict[str, Any]) -> Dict[str, Any]:
    single = select_tool(objective, step)
    actions = []
    if single.get("tool"):
        actions.append(
            {
                "tool": single.get("tool"),
                "payload": single.get("payload") or {},
            }
        )
    return {
        "actions": actions,
        "source": single.get("source", "single_tool"),
        "reason": single.get("reason", "Single tool route."),
        "confidence": float(single.get("confidence") or 0.5),
        "fallback_chains": [],
    }


def plan_tool_chain(objective: str, step: Dict[str, Any]) -> Dict[str, Any]:
    metadata = dict(step.get("metadata") or {})
    text = _step_text(objective, step)

    manual = _manual_chain(metadata)
    if manual:
        fallback = _single_tool_plan(objective, step)
        return {
            "actions": manual,
            "source": "manual_chain",
            "reason": "A manual tool chain was explicitly provided in step metadata.",
            "confidence": 1.0,
            "fallback_chains": [fallback] if fallback.get("actions") else [],
        }

    input_name = metadata.get("chain_input_name") or "chain_input.txt"
    output_name = metadata.get("chain_output_name") or "chain_output.txt"
    input_path = metadata.get("chain_input_path") or f"jarvis_stage3_artifacts/tool_runtime/outputs/{input_name}"
    output_path = metadata.get("chain_output_path") or f"jarvis_stage3_artifacts/tool_runtime/outputs/{output_name}"
    seed_content = metadata.get("chain_input_content") or f"Generated input for: {objective}"

    if (
        any(x in text for x in ["create", "write"]) and
        "read" in text and
        any(x in text for x in ["process", "transform", "summarize", "save"])
    ):
        return {
            "actions": [
                {
                    "tool": "file_write",
                    "payload": {
                        "path": input_path,
                        "content": seed_content,
                    },
                },
                {
                    "tool": "file_read",
                    "payload": {
                        "path": input_path,
                    },
                },
                {
                    "tool": "python",
                    "payload": {
                        "code": "text = {{previous_text_json}}\nprint(str(text).upper())",
                        "timeout_seconds": 20,
                    },
                },
                {
                    "tool": "file_write",
                    "payload": {
                        "path": output_path,
                        "content": "{{previous_text}}",
                    },
                },
            ],
            "source": "heuristic_chain",
            "reason": "Detected a create/read/process/save workflow; planned a four-step local chain.",
            "confidence": 0.95,
            "fallback_chains": [_single_tool_plan(objective, step)],
        }

    if any(x in text for x in ["api", "http", "request", "fetch"]) and any(x in text for x in ["process", "transform", "save"]):
        target_url = metadata.get("target_url") or "https://httpbin.org/get"
        return {
            "actions": [
                {
                    "tool": "http",
                    "payload": {
                        "method": "GET",
                        "url": target_url,
                        "timeout_seconds": 20,
                    },
                },
                {
                    "tool": "python",
                    "payload": {
                        "code": "text = {{previous_text_json}}\nprint(str(text)[:1000])",
                        "timeout_seconds": 20,
                    },
                },
                {
                    "tool": "file_write",
                    "payload": {
                        "path": output_path,
                        "content": "{{previous_text}}",
                    },
                },
            ],
            "source": "heuristic_chain",
            "reason": "Detected an HTTP/process/save workflow; planned a three-step chain.",
            "confidence": 0.90,
            "fallback_chains": [_single_tool_plan(objective, step)],
        }

    if "read" in text and any(x in text for x in ["process", "transform", "summarize", "save"]):
        target_path = metadata.get("target_path") or input_path
        return {
            "actions": [
                {
                    "tool": "file_read",
                    "payload": {
                        "path": target_path,
                    },
                },
                {
                    "tool": "python",
                    "payload": {
                        "code": "text = {{previous_text_json}}\nprint(str(text).upper())",
                        "timeout_seconds": 20,
                    },
                },
                {
                    "tool": "file_write",
                    "payload": {
                        "path": output_path,
                        "content": "{{previous_text}}",
                    },
                },
            ],
            "source": "heuristic_chain",
            "reason": "Detected a read/process/save workflow; planned a three-step local chain.",
            "confidence": 0.91,
            "fallback_chains": [_single_tool_plan(objective, step)],
        }

    return _single_tool_plan(objective, step)