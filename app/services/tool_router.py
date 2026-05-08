from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

import requests

from app.services.tool_registry import get_tool_names
from app.services.tool_safety import check_execution_mode, check_tool_allowed


DEFAULT_OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
DEFAULT_ROUTER_MODEL = (
    os.getenv("ROUTER_OLLAMA_MODEL")
    or os.getenv("OLLAMA_ROUTER_MODEL")
    or os.getenv("OLLAMA_MODEL")
    or "llama3.2:latest"
)


def _safe_text(*parts: Any) -> str:
    return " ".join(str(x or "") for x in parts).strip()


def _extract_url(text: str) -> Optional[str]:
    match = re.search(r"https?://[^\s\"'<>]+", text or "", flags=re.IGNORECASE)
    return match.group(0) if match else None


def _extract_filename(text: str) -> Optional[str]:
    match = re.search(r"([A-Za-z0-9_\-]+\.(txt|md|json|csv|log))", text or "", flags=re.IGNORECASE)
    return match.group(1) if match else None


def _tool_permitted(tool: str, metadata: Dict[str, Any]) -> bool:
    try:
        check_tool_allowed(tool, metadata)
        check_execution_mode(tool, metadata)
        return True
    except Exception:
        return False


def _normalize_candidate(
    tool: Optional[str],
    payload: Optional[Dict[str, Any]],
    source: str,
    reason: str,
    confidence: float,
    metadata: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if not tool:
        return None

    tool = str(tool).strip()
    if tool not in get_tool_names():
        return None

    if not _tool_permitted(tool, metadata):
        return None

    return {
        "tool": tool,
        "payload": payload or {},
        "source": source,
        "reason": reason,
        "confidence": round(float(confidence), 3),
    }


def _dedupe_candidates(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for item in items:
        key = (item.get("tool"), json.dumps(item.get("payload", {}), sort_keys=True, ensure_ascii=False))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _heuristic_candidates(objective: str, step: Dict[str, Any]) -> List[Dict[str, Any]]:
    metadata = dict(step.get("metadata") or {})
    title = step.get("title") or ""
    description = step.get("description") or ""
    task_type = step.get("task_type") or ""
    text = _safe_text(objective, title, description).lower()

    filename = metadata.get("target_filename") or _extract_filename(text) or "auto_file.txt"
    url = metadata.get("target_url") or _extract_url(text)
    candidates: List[Dict[str, Any]] = []

    # Manual override still wins but is handled outside.

    if any(x in text for x in ["create file", "write file", "save file", "artifact", "write artifact", "create a file"]):
        candidates.append({
            "tool": "file_write",
            "payload": {
                "path": f"jarvis_stage3_artifacts/tool_runtime/outputs/{filename}",
                "content": metadata.get("target_content") or f"Generated for: {objective}",
            },
            "source": "heuristic",
            "reason": "Detected a file creation/write intent in the objective or step description.",
            "confidence": 0.92,
        })

    if any(x in text for x in ["read file", "open file", "show file", "inspect file"]):
        candidates.append({
            "tool": "file_read",
            "payload": {
                "path": metadata.get("target_path") or f"jarvis_stage3_artifacts/tool_runtime/outputs/{filename}",
            },
            "source": "heuristic",
            "reason": "Detected a file read/inspect intent in the objective or step description.",
            "confidence": 0.90,
        })

    if url or any(x in text for x in ["api", "http", "request", "fetch url", "call endpoint"]):
        candidates.append({
            "tool": "http",
            "payload": {
                "method": metadata.get("http_method") or "GET",
                "url": url or metadata.get("target_url") or "https://httpbin.org/get",
                "timeout_seconds": metadata.get("http_timeout") or 20,
            },
            "source": "heuristic",
            "reason": "Detected an HTTP/API request intent or an explicit URL.",
            "confidence": 0.88,
        })

    if any(x in text for x in ["calculate", "compute", "python", "script", "transform", "parse json"]):
        candidates.append({
            "tool": "python",
            "payload": {
                "code": metadata.get("python_code") or "print('calculation result placeholder')",
                "timeout_seconds": metadata.get("python_timeout") or 20,
            },
            "source": "heuristic",
            "reason": "Detected a computation or scripting intent suitable for Python.",
            "confidence": 0.84,
        })

    if any(x in text for x in ["powershell", "shell command", "cmd", "list directory", "show files in folder"]):
        candidates.append({
            "tool": "shell",
            "payload": {
                "command": metadata.get("shell_command") or "Get-ChildItem .",
                "timeout_seconds": metadata.get("shell_timeout") or 15,
                "working_directory": metadata.get("working_directory") or ".",
            },
            "source": "heuristic",
            "reason": "Detected an explicit shell/PowerShell intent.",
            "confidence": 0.70,
        })

    if not candidates:
        # safest generic default
        candidates.append({
            "tool": "file_write",
            "payload": {
                "path": "jarvis_stage3_artifacts/tool_runtime/outputs/default_route.txt",
                "content": f"Default execution for: {objective}",
            },
            "source": "heuristic",
            "reason": "No strong signal found; using the safest generic file_write fallback.",
            "confidence": 0.55,
        })

    normalized = []
    for item in candidates:
        candidate = _normalize_candidate(
            tool=item.get("tool"),
            payload=item.get("payload"),
            source=item.get("source", "heuristic"),
            reason=item.get("reason", ""),
            confidence=item.get("confidence", 0.5),
            metadata=metadata,
        )
        if candidate:
            normalized.append(candidate)

    return _dedupe_candidates(normalized)


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None

    try:
        return json.loads(text)
    except Exception:
        pass

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None

    try:
        return json.loads(match.group(0))
    except Exception:
        return None


def _llm_candidate(objective: str, step: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    metadata = dict(step.get("metadata") or {})
    router_mode = str(metadata.get("router_mode") or "hybrid").strip().lower()
    if router_mode not in {"hybrid", "llm", "auto"}:
        return None

    prompt = {
        "objective": objective,
        "step": {
            "title": step.get("title"),
            "description": step.get("description"),
            "task_type": step.get("task_type"),
            "metadata": metadata,
        },
        "allowed_tools": get_tool_names(),
        "instruction": (
            "Return ONLY JSON with keys: tool, payload, reason, confidence. "
            "Choose the safest valid tool. "
            "Confidence must be between 0 and 1."
        ),
    }

    try:
        response = requests.post(
            f"{DEFAULT_OLLAMA_URL}/api/generate",
            json={
                "model": metadata.get("router_model") or DEFAULT_ROUTER_MODEL,
                "prompt": json.dumps(prompt, ensure_ascii=False),
                "stream": False,
                "format": "json",
            },
            timeout=float(metadata.get("router_timeout") or 12),
        )
        response.raise_for_status()
        data = response.json()
        raw = data.get("response") or ""
        parsed = _extract_json_object(raw)
        if not isinstance(parsed, dict):
            return None

        candidate = _normalize_candidate(
            tool=parsed.get("tool"),
            payload=parsed.get("payload") or {},
            source="llm",
            reason=str(parsed.get("reason") or "LLM selected this tool."),
            confidence=float(parsed.get("confidence") or 0.5),
            metadata=metadata,
        )
        return candidate
    except Exception:
        return None


def select_tool(objective: str, step: Dict[str, Any]) -> Dict[str, Any]:
    metadata = dict(step.get("metadata") or {})

    # Manual override stays strongest.
    if metadata.get("tool"):
        candidate = _normalize_candidate(
            tool=metadata.get("tool"),
            payload=metadata.get("tool_payload") or {},
            source="manual",
            reason="Tool was explicitly provided in step metadata.",
            confidence=1.0,
            metadata=metadata,
        )
        if candidate:
            return {**candidate, "fallbacks": []}

    heuristics = _heuristic_candidates(objective, step)
    llm_candidate = _llm_candidate(objective, step)

    candidates: List[Dict[str, Any]] = []
    if llm_candidate:
        candidates.append(llm_candidate)

    candidates.extend(heuristics)
    candidates = _dedupe_candidates(candidates)

    if not candidates:
        return {
            "tool": None,
            "payload": {},
            "source": "none",
            "reason": "No valid tool candidate was produced.",
            "confidence": 0.0,
            "fallbacks": [],
        }

    primary = candidates[0]
    fallbacks = candidates[1:]

    return {
        **primary,
        "fallbacks": fallbacks,
    }