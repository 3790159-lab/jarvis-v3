from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.services.tool_retry_executor import execute_with_retry


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


def _extract_previous_text(result: Optional[Dict[str, Any]]) -> str:
    if not isinstance(result, dict):
        return ""

    output = result.get("output")
    if isinstance(output, dict):
        for key in ["content", "body", "stdout", "stderr", "path"]:
            if key not in output:
                continue
            value = output.get(key)
            if isinstance(value, str):
                return value
            try:
                return json.dumps(value, ensure_ascii=False)
            except Exception:
                return str(value)

    if output is None:
        return ""

    if isinstance(output, str):
        return output

    try:
        return json.dumps(output, ensure_ascii=False)
    except Exception:
        return str(output)


def _extract_previous_path(result: Optional[Dict[str, Any]]) -> str:
    if not isinstance(result, dict):
        return ""
    output = result.get("output")
    if isinstance(output, dict):
        path = output.get("path")
        if isinstance(path, str):
            return path
    return ""


def _hydrate_string(value: str, previous_result: Optional[Dict[str, Any]], objective: str, step_description: str) -> str:
    previous_text = _extract_previous_text(previous_result)
    previous_path = _extract_previous_path(previous_result)
    previous_json = json.dumps(previous_result or {}, ensure_ascii=False)
    previous_text_json = json.dumps(previous_text, ensure_ascii=False)

    hydrated = value
    hydrated = hydrated.replace("{{objective}}", objective)
    hydrated = hydrated.replace("{{step_description}}", step_description)
    hydrated = hydrated.replace("{{previous_text}}", previous_text)
    hydrated = hydrated.replace("{{previous_path}}", previous_path)
    hydrated = hydrated.replace("{{previous_json}}", previous_json)
    hydrated = hydrated.replace("{{previous_text_json}}", previous_text_json)
    return hydrated


def _hydrate_value(value: Any, previous_result: Optional[Dict[str, Any]], objective: str, step_description: str) -> Any:
    if isinstance(value, str):
        return _hydrate_string(value, previous_result, objective, step_description)
    if isinstance(value, dict):
        return {
            str(k): _hydrate_value(v, previous_result, objective, step_description)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_hydrate_value(v, previous_result, objective, step_description) for v in value]
    return value


def _apply_defaults(tool: str, payload: Dict[str, Any], previous_result: Optional[Dict[str, Any]], objective: str) -> Dict[str, Any]:
    out = dict(payload or {})
    previous_text = _extract_previous_text(previous_result)
    previous_path = _extract_previous_path(previous_result)

    if tool == "file_write":
        out.setdefault("path", "jarvis_stage3_artifacts/tool_runtime/outputs/chain_output.txt")
        if not out.get("content"):
            out["content"] = previous_text or f"Generated for: {objective}"

    if tool == "file_read":
        if not out.get("path") and previous_path:
            out["path"] = previous_path

    if tool == "python":
        out.setdefault("timeout_seconds", 20)
        out.setdefault("code", "print('no python code provided')")

    if tool == "http":
        out.setdefault("method", "GET")
        out.setdefault("timeout_seconds", 20)

    if tool == "shell":
        out.setdefault("timeout_seconds", 15)
        out.setdefault("working_directory", ".")

    return out


def execute_tool_chain(
    actions: List[Dict[str, Any]],
    metadata: Dict[str, Any],
    objective: str,
    step_description: str,
) -> Dict[str, Any]:
    execution_attempts: List[Dict[str, Any]] = []
    previous_result: Optional[Dict[str, Any]] = None
    final_result: Optional[Dict[str, Any]] = None

    if not actions:
        return {
            "ok": False,
            "final_result": None,
            "execution_attempts": [],
            "error": "No tool chain actions were provided",
            "actions_executed": 0,
        }

    for index, action in enumerate(actions, start=1):
        tool = str(action.get("tool") or "").strip()
        payload = dict(action.get("payload") or {})

        hydrated = _hydrate_value(payload, previous_result, objective, step_description)
        hydrated = _apply_defaults(tool, hydrated, previous_result, objective)

        try:
            result = execute_with_retry(tool, hydrated, dict(metadata))
        except Exception as exc:
            result = {
                "ok": False,
                "tool": tool,
                "output": None,
                "error": str(exc),
                "attempt": 1,
            }

        execution_attempts.append(
            _json_safe(
                {
                    "index": index,
                    "tool": tool,
                    "payload": hydrated,
                    "result": result,
                }
            )
        )

        final_result = _json_safe(result)
        previous_result = result

        if not result.get("ok", False):
            return {
                "ok": False,
                "final_result": final_result,
                "execution_attempts": execution_attempts,
                "error": result.get("error") or "Chain action failed",
                "actions_executed": index,
            }

    return {
        "ok": True,
        "final_result": final_result,
        "execution_attempts": execution_attempts,
        "error": None,
        "actions_executed": len(actions),
    }