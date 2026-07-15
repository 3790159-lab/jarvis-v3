from __future__ import annotations

import os
import time
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from pathlib import Path

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.services.api_auth import require_api_key

router = APIRouter(prefix="/api/cloud", tags=["cloud_control"])


def _internal_key() -> str:
    """The API key this proxy presents to (now-gated) internal targets."""
    return (
        os.getenv("JARVIS_INTERNAL_API_KEY", "").strip()
        or os.getenv("JARVIS_ADMIN_KEY", "").strip()
    )


def _backend_base() -> str:
    # Hardened runtime base resolution for unified cloud proxy.
    explicit = (os.getenv("BACKEND_BASE_URL") or "").strip()
    if explicit:
        if explicit in ("http://127.0.0.1:8010", "http://localhost:8010"):
            return "http://127.0.0.1:8015"
        return explicit.rstrip("/")

    host = (os.getenv("APP_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port = (os.getenv("APP_PORT") or "8015").strip() or "8015"

    if port == "8010":
        port = "8015"

    return f"http://{host}:{port}"

def _call(method: str, path: str, payload: Optional[dict] = None, timeout: int = 120) -> Any:
    url = _backend_base() + path
    method = method.upper().strip()

    # Forward the internal API key so proxied calls authenticate to gated
    # targets instead of bypassing their auth. The proxy is thus only ever as
    # privileged as an authenticated caller.
    headers = {}
    key = _internal_key()
    if key:
        headers["X-API-Key"] = key

    try:
        if method == "GET":
            response = requests.get(url, timeout=timeout, headers=headers)
        elif method == "POST":
            response = requests.post(url, json=payload or {}, timeout=timeout, headers=headers)
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported method: {method}")

        content_type = response.headers.get("content-type", "")
        if response.status_code >= 400:
            try:
                detail = response.json()
            except Exception:
                detail = response.text
            raise HTTPException(
                status_code=response.status_code,
                detail={
                    "path": path,
                    "status_code": response.status_code,
                    "response": detail,
                },
            )

        if "application/json" in content_type:
            return response.json()

        return {
            "status": "ok",
            "text": response.text,
        }
    except HTTPException:
        raise
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Proxy call failed for {path}: {exc}") from exc


class CloudExecuteRequest(BaseModel):
    target: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = 120
    dry_run: bool = False
    route_override: Optional[str] = None


class RawInvokeRequest(BaseModel):
    method: str = "POST"
    path: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = 120


TARGET_MAP = {
    "tool": "/api/tools/execute",
    "agent": "/api/agents/invoke",
    "autonomy_tool": "/api/autonomy/tools/execute",
    "chain": "/api/autonomy/chains/execute",
    "workflow": "/api/autonomy/workflows/execute",
    "graph": "/api/autonomy/graphs/execute",
    "repair": "/api/autonomy/repairs/execute",
    "external_executor": "/api/external-executor/execute",
    "google": "/api/google/execute",
    "spreadsheet": "/api/spreadsheets/execute",
    "respond": "/respond",
    "runtime_goal": "/api/runtime-bridge/submit",
    "mission_graph": "/api/mission-graph/submit",
    "multi_step": "/api/multi-step/execute",
    "planner": "/api/execution-planner/plan",
}

_ALLOWED_RAW_PREFIXES = (
    "/api/",
    "/health",
    "/respond",
    "/missions",
    "/memory",
    "/policy",
    "/routes/debug",
)


@router.get("/health")
def cloud_health():
    probes = {
        "core": "/health",
        "ai": "/api/ai/health",
        "tools": "/api/tools/health",
        "autonomy": "/api/autonomy/health",
        "external_executor": "/api/external-executor/health",
        "google": "/api/google/health",
        "spreadsheets": "/api/spreadsheets/health",
    }

    result = {
        "status": "ok",
        "backend_base": _backend_base(),
        "probes": {},
    }

    for name, path in probes.items():
        try:
            result["probes"][name] = _call("GET", path, timeout=20)
        except Exception as exc:
            result["probes"][name] = {
                "status": "error",
                "detail": str(exc),
            }

    return result


@router.get("/capabilities")
def cloud_capabilities():
    openapi = _call("GET", "/openapi.json", timeout=30)
    paths = sorted((openapi.get("paths") or {}).keys())

    return {
        "status": "ok",
        "backend_base": _backend_base(),
        "targets": TARGET_MAP,
        "published_routes": paths,
        "summary": {
            "route_count": len(paths),
            "has_tools_registry": "/api/tools/registry" in paths,
            "has_tools_execute": "/api/tools/execute" in paths,
            "has_agents_invoke": "/api/agents/invoke" in paths,
            "has_autonomy_health": "/api/autonomy/health" in paths,
            "has_external_executor": "/api/external-executor/execute" in paths,
            "has_google_execute": "/api/google/execute" in paths,
            "has_spreadsheet_execute": "/api/spreadsheets/execute" in paths,
            "has_respond": "/respond" in paths,
            "has_legacy_api_respond": "/api/respond" in paths,
            "has_legacy_api_goals": "/api/goals" in paths,
            "has_legacy_api_agents": "/api/agents" in paths,
        },
    }


@router.get("/tools")
def cloud_tools():
    registry = _call("GET", "/api/tools/registry", timeout=30)
    return {
        "status": "ok",
        "registry": registry,
    }


@router.get("/agents")
def cloud_agents():
    registry = None
    adapters = None

    try:
        registry = _call("GET", "/api/agents/registry", timeout=30)
    except Exception as exc:
        registry = {"status": "error", "detail": str(exc)}

    try:
        adapters = _call("GET", "/api/agents/adapters", timeout=30)
    except Exception as exc:
        adapters = {"status": "error", "detail": str(exc)}

    return {
        "status": "ok",
        "registry": registry,
        "adapters": adapters,
    }


@router.post("/execute")
def cloud_execute(request: CloudExecuteRequest, _key: str = Depends(require_api_key)):
    target = str(request.target or "").strip()
    payload = dict(request.payload or {})

    if target == "respond":
        text = str(
            payload.get("message")
            or payload.get("text")
            or payload.get("prompt")
            or payload.get("query")
            or payload.get("input")
            or ""
        ).strip()

        payload = {
            "message": text,
            "text": text,
            "prompt": text,
            "query": text,
            "input": text,
            **payload,
        }

    if target == "agent":
        payload = _normalize_agent_payload(payload)

    if target == "tool":
        payload = _normalize_tool_payload(payload)

    if target == "codegen":
        payload = _normalize_codegen_payload(payload)

    path = request.route_override or TARGET_MAP.get(target)
    if target == "codegen" and not path:
        path = "direct://codegen"

    if not path:
        raise HTTPException(
            status_code=400,
            detail={
                "message": f"Unknown target: {target}",
                "known_targets": sorted(list(set(list(TARGET_MAP.keys()) + ["codegen"]))),
            },
        )

    if request.dry_run:
        result = {
            "status": "dry_run",
            "target": target,
            "resolved_path": path,
            "payload": payload,
            "timeout_seconds": request.timeout_seconds,
        }
        _append_audit({
            "kind": "cloud_execute",
            "dry_run": True,
            "target": target,
            "resolved_path": path,
            "payload_keys": sorted(list(payload.keys())),
            "status": "ok",
            "result_kind": "dry_run",
        })
        return result

    try:
        # -------------------------------------------------
        # CODEGEN: dedicated path, no mission/planner fallback
        # -------------------------------------------------
        if target == "codegen":
            result = _cloud_codegen(payload, request.timeout_seconds)
            text = _normalize_generated_text(result.get("text") if isinstance(result, dict) else "")

            if not text:
                raise RuntimeError("Codegen returned empty text.")

            _append_audit({
                "kind": "cloud_execute",
                "dry_run": False,
                "target": target,
                "resolved_path": path,
                "payload_keys": sorted(list(payload.keys())),
                "status": "ok",
                "result_kind": f"codegen_{result.get('provider', 'unknown')}",
            })

            return {
                "status": "ok",
                "target": target,
                "resolved_path": path,
                "result": result,
                "normalized_text": text,
            }

        # -------------------------------------------------
        # AGENT: retry empty Ollama outputs, then fallback
        # -------------------------------------------------
        if target == "agent":
            attempts = 3 if str(payload.get("adapter_name") or "") == "ollama_http" else 1
            last_result = None
            last_text = ""

            for _ in range(attempts):
                last_result = _call("POST", path, payload=payload, timeout=request.timeout_seconds)
                last_text = _extract_agent_text_any(last_result)

                if isinstance(last_result, dict):
                    inner = last_result.get("result")
                    if isinstance(inner, dict) and inner.get("status") == "approval_required":
                        _append_audit({
                            "kind": "cloud_execute",
                            "dry_run": False,
                            "target": target,
                            "resolved_path": path,
                            "payload_keys": sorted(list(payload.keys())),
                            "status": "approval_required",
                            "result_kind": "approval",
                        })
                        return {
                            "status": "ok",
                            "target": target,
                            "resolved_path": path,
                            "result": last_result,
                            "normalized_text": "",
                        }

                if last_text:
                    _append_audit({
                        "kind": "cloud_execute",
                        "dry_run": False,
                        "target": target,
                        "resolved_path": path,
                        "payload_keys": sorted(list(payload.keys())),
                        "status": "ok",
                        "result_kind": "agent_text",
                    })
                    return {
                        "status": "ok",
                        "target": target,
                        "resolved_path": path,
                        "result": last_result,
                        "normalized_text": last_text,
                    }

                time.sleep(1.5)

            fallback = _agent_fallback_respond(payload, request.timeout_seconds)
            fallback_text = _extract_agent_text_any(fallback) or str(
                fallback.get("reply") if isinstance(fallback, dict) else ""
            ).strip()

            _append_audit({
                "kind": "cloud_execute",
                "dry_run": False,
                "target": target,
                "resolved_path": path,
                "payload_keys": sorted(list(payload.keys())),
                "status": "ok_with_fallback",
                "result_kind": "agent_fallback_respond",
            })
            return {
                "status": "ok",
                "target": target,
                "resolved_path": path,
                "result": last_result,
                "fallback_result": fallback,
                "normalized_text": fallback_text,
            }

        # -------------------------------------------------
        # TOOL: normalize schema + safe local file fallback
        # -------------------------------------------------
        if target == "tool":
            tool_name = str(payload.get("tool") or "").strip()

            if tool_name == "file_write":
                result = _safe_local_file_write(payload.get("path"), payload.get("content"))
                _append_audit({
                    "kind": "cloud_execute",
                    "dry_run": False,
                    "target": target,
                    "resolved_path": path,
                    "payload_keys": sorted(list(payload.keys())),
                    "status": "ok",
                    "result_kind": "safe_local_file_write",
                })
                return {
                    "status": "ok",
                    "target": target,
                    "resolved_path": path,
                    "result": result,
                }

            if tool_name == "file_read":
                result = _safe_local_file_read(payload.get("path"))
                _append_audit({
                    "kind": "cloud_execute",
                    "dry_run": False,
                    "target": target,
                    "resolved_path": path,
                    "payload_keys": sorted(list(payload.keys())),
                    "status": "ok" if result.get("ok") else "error",
                    "result_kind": "safe_local_file_read",
                })
                return {
                    "status": "ok",
                    "target": target,
                    "resolved_path": path,
                    "result": result,
                }

            result = _call("POST", path, payload=payload, timeout=request.timeout_seconds)
            _append_audit({
                "kind": "cloud_execute",
                "dry_run": False,
                "target": target,
                "resolved_path": path,
                "payload_keys": sorted(list(payload.keys())),
                "status": "ok",
                "result_kind": "tool_execute",
            })
            return {
                "status": "ok",
                "target": target,
                "resolved_path": path,
                "result": result,
            }

        # -------------------------------------------------
        # DEFAULT
        # -------------------------------------------------
        result = _call("POST", path, payload=payload, timeout=request.timeout_seconds)
        _append_audit({
            "kind": "cloud_execute",
            "dry_run": False,
            "target": target,
            "resolved_path": path,
            "payload_keys": sorted(list(payload.keys())),
            "status": "ok",
            "result_kind": "default",
        })
        return {
            "status": "ok",
            "target": target,
            "resolved_path": path,
            "result": result,
        }

    except Exception as exc:
        _append_audit({
            "kind": "cloud_execute",
            "dry_run": False,
            "target": target,
            "resolved_path": path,
            "payload_keys": sorted(list(payload.keys())),
            "status": "error",
            "error": str(exc),
            "result_kind": "error",
        })
        raise

@router.post("/raw-invoke")
def cloud_raw_invoke(request: RawInvokeRequest, _key: str = Depends(require_api_key)):
    path = request.path.strip()
    if not path.startswith("/"):
        raise HTTPException(status_code=400, detail="Path must start with '/'")

    if not any(path.startswith(prefix) for prefix in _ALLOWED_RAW_PREFIXES):
        raise HTTPException(
            status_code=403,
            detail={
                "message": "Path is outside the allowed prefixes",
                "allowed_prefixes": list(_ALLOWED_RAW_PREFIXES),
            },
        )

    result = _call(request.method, path, payload=request.payload, timeout=request.timeout_seconds)
    return {
        "status": "ok",
        "method": request.method.upper(),
        "path": path,
        "result": result,
    }

AUDIT_FILE = Path("state/cloud_audit/cloud_execution_audit.jsonl")
AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)

SAFE_TOOL_TIMEOUTS = {
    "shell": 20,
    "python": 20,
    "http": 30,
    "file_read": 10,
    "file_write": 10,
}

SAFE_SHELL_DENYLIST = [
    "del ",
    "erase ",
    "format ",
    "shutdown ",
    "restart-computer",
    "stop-computer",
    "remove-item -recurse",
    "rd /s",
    "rmdir /s",
]

def _append_audit(event: dict) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        **event,
    }
    with AUDIT_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

def _harden_tool_payload(payload: dict) -> dict:
    payload = dict(payload or {})
    tool_name = str(payload.get("tool_name") or payload.get("name") or "").strip()

    if not tool_name:
        return payload

    timeout_default = SAFE_TOOL_TIMEOUTS.get(tool_name, 30)
    current_timeout = payload.get("timeout_seconds")
    if current_timeout is None:
        payload["timeout_seconds"] = timeout_default
    else:
        try:
            payload["timeout_seconds"] = min(int(current_timeout), max(timeout_default, 60))
        except Exception:
            payload["timeout_seconds"] = timeout_default

    if tool_name == "shell":
        command = str(payload.get("command") or "")
        lowered = command.lower()
        for bad in SAFE_SHELL_DENYLIST:
            if bad in lowered:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "message": "Blocked potentially dangerous shell command in safe operator mode",
                        "command": command,
                        "matched_rule": bad,
                    },
                )

    if tool_name == "http":
        url = str(payload.get("url") or "")
        if not (url.startswith("http://") or url.startswith("https://")):
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "HTTP tool requires absolute http/https URL",
                    "url": url,
                },
            )

    if tool_name in ("file_read", "file_write"):
        path = str(payload.get("path") or "")
        if not path:
            raise HTTPException(
                status_code=400,
                detail={"message": f"{tool_name} requires path"},
            )

    return payload


@router.get("/audit/recent")
def cloud_audit_recent(limit: int = 20):
    if not AUDIT_FILE.exists():
        return {"status": "ok", "items": []}

    lines = AUDIT_FILE.read_text(encoding="utf-8").splitlines()
    items = []
    for line in lines[-max(1, min(limit, 200)):]:
        try:
            items.append(json.loads(line))
        except Exception:
            items.append({"raw": line, "parse_error": True})
    return {"status": "ok", "items": items}


@router.get("/operator-mode")
def cloud_operator_mode():
    return {
        "status": "ok",
        "mode": "safe_operator",
        "shell_denylist": SAFE_SHELL_DENYLIST,
        "tool_timeout_defaults": SAFE_TOOL_TIMEOUTS,
        "audit_file": str(AUDIT_FILE),
    }


@router.post("/plan-and-execute")
def cloud_plan_and_execute(payload: dict, _key: str = Depends(require_api_key)):
    goal = str(payload.get("goal") or "").strip()
    if not goal:
        raise HTTPException(status_code=400, detail={"message": "goal is required"})

    plan = _call(
        "POST",
        "/api/execution-planner/plan",
        payload={"goal": goal, "context": payload.get("context") or {}},
        timeout=60,
    )

    executions = []
    for step in plan.get("steps", []):
        tool_name = str(step.get("tool_name") or "").strip()

        if tool_name == "respond":
            exec_payload = _build_safe_tool_payload_from_step(step, goal)
            exec_result = _call("POST", "/respond", payload=exec_payload, timeout=120)
            executions.append({"step": step, "result": exec_result})
            continue

        if tool_name in ("http", "file_read"):
            tool_payload = _build_safe_tool_payload_from_step(step, goal)
            exec_result = _call("POST", "/api/tools/execute", payload=tool_payload, timeout=60)
            executions.append({"step": step, "result": exec_result})
            continue

        executions.append({
            "step": step,
            "result": {
                "status": "planned_only",
                "message": "Tool step detected but not in safe auto-execute allowlist.",
            },
        })

    _append_audit({
        "kind": "plan_and_execute",
        "goal": goal,
        "status": "ok",
        "step_count": len(plan.get("steps", [])),
        "result_kind": "plan_bundle",
    })

    return {
        "status": "ok",
        "goal": goal,
        "plan": plan,
        "executions": executions,
    }


# SAFE_AGENT_TOOL_COMPAT_V3

SAFE_RUNTIME_ROOTS = [
    Path("state/runtime_exec").resolve(),
    Path("state/obsidian_runtime").resolve(),
]

def _extract_agent_text_any(value: Any) -> str:
    try:
        # direct dict/object walk for common agent result layouts
        candidates = []

        def grab(obj, *path):
            cur = obj
            for key in path:
                if cur is None:
                    return None
                if isinstance(cur, dict):
                    cur = cur.get(key)
                else:
                    cur = getattr(cur, key, None)
            return cur

        candidates.extend([
            grab(value, "result", "result", "output", "text"),
            grab(value, "result", "result", "output", "raw", "response"),
            grab(value, "result", "result", "text"),
            grab(value, "result", "result", "reply"),
            grab(value, "result", "output", "text"),
            grab(value, "result", "output", "raw", "response"),
            grab(value, "result", "text"),
            grab(value, "result", "reply"),
            grab(value, "output", "text"),
            grab(value, "output", "raw", "response"),
            grab(value, "text"),
            grab(value, "reply"),
        ])

        for item in candidates:
            if isinstance(item, str) and item.strip():
                return item.strip()
    except Exception:
        pass
    return ""

def _normalize_agent_payload(payload: dict) -> dict:
    payload = dict(payload or {})

    # input aliases
    text = str(
        payload.get("input")
        or payload.get("message")
        or payload.get("text")
        or payload.get("prompt")
        or payload.get("query")
        or ""
    ).strip()
    if text:
        payload["input"] = text

    # infer adapter if only agent_id is known
    agent_id = str(payload.get("agent_id") or "")
    if not payload.get("adapter_name"):
        if "ollama" in agent_id:
            payload["adapter_name"] = "ollama_http"
        elif "openai" in agent_id:
            payload["adapter_name"] = "openai_compatible_http"
        elif "claude" in agent_id:
            payload["adapter_name"] = "claude_code_bridge"
        elif "echo" in agent_id:
            payload["adapter_name"] = "local_echo"

    return payload

def _normalize_tool_payload(payload: dict) -> dict:
    payload = dict(payload or {})

    # flatten arguments if provided
    args = payload.get("arguments")
    if isinstance(args, dict):
        merged = dict(args)
        for k, v in payload.items():
            if k != "arguments" and k not in merged:
                merged[k] = v
        payload = merged

    # normalize tool field names
    if payload.get("tool_name") and not payload.get("tool"):
        payload["tool"] = payload["tool_name"]

    # cap timeout
    try:
        if "timeout_seconds" in payload:
            payload["timeout_seconds"] = max(1, min(int(payload["timeout_seconds"]), 60))
    except Exception:
        payload["timeout_seconds"] = 15

    return payload

def _safe_resolve_runtime_path(path_value: str) -> Path:
    rel = str(path_value or "").replace("\\", "/").lstrip("/")
    if not rel:
        rel = "state/runtime_exec/output.txt"

    # force writes/reads into allowed runtime trees
    if not (rel.startswith("state/runtime_exec/") or rel.startswith("state/obsidian_runtime/")):
        rel = "state/runtime_exec/" + Path(rel).name

    dest = (Path.cwd() / rel).resolve()

    for root in SAFE_RUNTIME_ROOTS:
        try:
            if str(dest).startswith(str(root)):
                dest.parent.mkdir(parents=True, exist_ok=True)
                return dest
        except Exception:
            pass

    # final fallback
    fallback = (Path.cwd() / "state" / "runtime_exec" / Path(rel).name).resolve()
    fallback.parent.mkdir(parents=True, exist_ok=True)
    return fallback

def _safe_local_file_write(path_value: str, content_value: Any) -> dict:
    dest = _safe_resolve_runtime_path(path_value)
    text = "" if content_value is None else str(content_value)
    dest.write_text(text, encoding="utf-8")
    return {
        "ok": True,
        "tool": "file_write",
        "output": {
            "path": str(dest),
            "bytes": len(text.encode("utf-8")),
        },
        "error": None,
        "exit_code": 0,
        "duration_ms": 0,
        "metadata": {
            "mode": "safe_local_fallback",
        },
    }

def _safe_local_file_read(path_value: str) -> dict:
    dest = _safe_resolve_runtime_path(path_value)
    if not dest.exists():
        return {
            "ok": False,
            "tool": "file_read",
            "output": None,
            "error": f"File not found: {dest}",
            "exit_code": 1,
            "duration_ms": 0,
            "metadata": {
                "mode": "safe_local_fallback",
            },
        }

    text = dest.read_text(encoding="utf-8")
    return {
        "ok": True,
        "tool": "file_read",
        "output": {
            "path": str(dest),
            "content": text,
        },
        "error": None,
        "exit_code": 0,
        "duration_ms": 0,
        "metadata": {
            "mode": "safe_local_fallback",
        },
    }

def _agent_fallback_respond(payload: dict, timeout_seconds: int) -> dict:
    prompt = str(payload.get("input") or payload.get("message") or "").strip()
    return _call(
        "POST",
        "/respond",
        payload={
            "message": prompt,
            "text": prompt,
            "prompt": prompt,
            "query": prompt,
            "input": prompt,
        },
        timeout=timeout_seconds,
    )


# SAFE_CLOUD_CODEGEN_V1

CODEGEN_PROVIDER_DEFAULT = os.getenv("CLOUD_CODEGEN_PROVIDER", "ollama").strip().lower()
CODEGEN_MODEL_DEFAULT = os.getenv("CLOUD_CODEGEN_MODEL", "llama3.2:latest").strip()
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")

_PLANNER_MARKERS = [
    "mission/task intent",
    "mission_id:",
    "goal_id:",
    "status: planned",
    "long-autonomy",
    "mission planned",
    "tasks queued",
]

def _normalize_generated_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.strip()

    if text.startswith("```html"):
        text = re.sub(r"^\s*```html\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```\s*$", "", text)
    elif text.startswith("```"):
        text = re.sub(r"^\s*```\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```\s*$", "", text)

    return text.strip()

def _looks_like_planner_text(text: str) -> bool:
    t = (text or "").lower()
    return any(marker in t for marker in _PLANNER_MARKERS)

def _looks_like_code(text: str, language: str = "html") -> bool:
    t = (text or "").strip()
    if not t:
        return False

    lang = (language or "html").strip().lower()

    if lang == "html":
        return (
            "<!doctype html" in t.lower()
            or "<html" in t.lower()
            or ("<head" in t.lower() and "<body" in t.lower())
        )

    if lang in ("python", "py"):
        return any(token in t for token in ["def ", "class ", "import ", "if __name__ == "])

    if lang in ("javascript", "js"):
        return any(token in t for token in ["function ", "const ", "let ", "document.", "window."])

    return len(t) >= 40

def _normalize_codegen_payload(payload: dict) -> dict:
    payload = dict(payload or {})

    text = str(
        payload.get("input")
        or payload.get("message")
        or payload.get("text")
        or payload.get("prompt")
        or payload.get("query")
        or ""
    ).strip()

    payload["input"] = text
    payload["language"] = str(payload.get("language") or "html").strip().lower()

    provider = str(
        payload.get("provider")
        or payload.get("adapter_name")
        or CODEGEN_PROVIDER_DEFAULT
        or "ollama"
    ).strip().lower()

    if provider in ("cloud", "claude", "claude_code", "claude_code_bridge"):
        provider = "cloud"

    if provider in ("ollama_http", "ollama"):
        provider = "ollama"

    payload["provider"] = provider
    payload["model"] = str(payload.get("model") or CODEGEN_MODEL_DEFAULT).strip()

    try:
        payload["num_predict"] = max(256, min(int(payload.get("num_predict") or 5000), 12000))
    except Exception:
        payload["num_predict"] = 5000

    try:
        payload["temperature"] = float(payload.get("temperature") or 0.2)
    except Exception:
        payload["temperature"] = 0.2

    allow_provider_fallback = payload.get("allow_provider_fallback")
    if allow_provider_fallback is None:
        payload["allow_provider_fallback"] = True
    else:
        payload["allow_provider_fallback"] = bool(allow_provider_fallback)

    return payload

def _direct_ollama_codegen(payload: dict, timeout_seconds: int) -> dict:
    prompt = str(payload.get("input") or "").strip()
    language = str(payload.get("language") or "html").strip().lower()
    model = str(payload.get("model") or CODEGEN_MODEL_DEFAULT).strip()

    if not prompt:
        raise ValueError("Codegen prompt is empty.")

    if language == "html":
        prompt = (
            "Return only a complete HTML document. "
            "No markdown fences. No explanations. "
            "The output must begin with <!DOCTYPE html> or <html>.\n\n"
            + prompt
        )

    body = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": float(payload.get("temperature") or 0.2),
            "num_predict": int(payload.get("num_predict") or 5000),
        },
    }

    response = requests.post(
        f"{OLLAMA_BASE_URL}/api/generate",
        json=body,
        timeout=max(int(timeout_seconds or 120), 60),
    )
    response.raise_for_status()

    data = response.json()
    text = _normalize_generated_text(data.get("response"))

    if not text:
        raise RuntimeError("Ollama codegen returned empty text.")

    if _looks_like_planner_text(text):
        raise RuntimeError("Ollama codegen returned planner text instead of code.")

    if not _looks_like_code(text, language):
        raise RuntimeError(f"Ollama codegen did not return valid {language} content.")

    return {
        "ok": True,
        "provider": "ollama",
        "model": model,
        "language": language,
        "text": text,
        "raw": data,
    }


# SAFE_CLOUD_CODEGEN_V2

def _collect_codegen_strings(value, sink):
    try:
        if value is None:
            return
        if isinstance(value, str):
            v = value.strip()
            if v:
                sink.append(v)
            return
        if isinstance(value, dict):
            for v in value.values():
                _collect_codegen_strings(v, sink)
            return
        if isinstance(value, (list, tuple, set)):
            for v in value:
                _collect_codegen_strings(v, sink)
            return
    except Exception:
        return

def _extract_cloud_codegen_text(result: Any, language: str = "html") -> str:
    direct = _normalize_generated_text(_extract_agent_text_any(result))
    if direct and (not _looks_like_planner_text(direct)) and _looks_like_code(direct, language):
        return direct

    strings = []
    _collect_codegen_strings(result, strings)

    for item in strings:
        text = _normalize_generated_text(item)
        if text and (not _looks_like_planner_text(text)) and _looks_like_code(text, language):
            return text

        fence_match = re.search(r"```(?:html|xml|javascript|js|python)?\s*(.*?)```", item, flags=re.IGNORECASE | re.DOTALL)
        if fence_match:
            fenced = _normalize_generated_text(fence_match.group(1))
            if fenced and (not _looks_like_planner_text(fenced)) and _looks_like_code(fenced, language):
                return fenced

        if (language or "html").lower() == "html":
            html_match = re.search(r"<!DOCTYPE html[\s\S]*?</html>|<html[\s\S]*?</html>", item, flags=re.IGNORECASE)
            if html_match:
                html_text = _normalize_generated_text(html_match.group(0))
                if html_text and (not _looks_like_planner_text(html_text)) and _looks_like_code(html_text, language):
                    return html_text

    joined = "\n\n".join(strings)
    if (language or "html").lower() == "html":
        html_match = re.search(r"<!DOCTYPE html[\s\S]*?</html>|<html[\s\S]*?</html>", joined, flags=re.IGNORECASE)
        if html_match:
            html_text = _normalize_generated_text(html_match.group(0))
            if html_text and (not _looks_like_planner_text(html_text)) and _looks_like_code(html_text, language):
                return html_text

    return ""

def _write_cloud_codegen_debug(result: Any, suffix: str = "latest") -> str:
    try:
        base = Path("state/runtime_exec").resolve()
        base.mkdir(parents=True, exist_ok=True)
        dest = base / f"cloud_codegen_debug_{suffix}.json"
        with dest.open("w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)
        return str(dest)
    except Exception:
        return ""



def _direct_cloud_codegen(payload: dict, timeout_seconds: int) -> dict:
    prompt = str(payload.get("input") or "").strip()
    language = str(payload.get("language") or "html").strip().lower()
    model = str(payload.get("model") or "").strip()

    if not prompt:
        raise ValueError("Codegen prompt is empty.")

    adapters = payload.get("cloud_adapters") or [
        "claude_code_bridge",
        "openai_compatible_http",
    ]

    errors = []
    last_result = None

    for adapter_name in adapters:
        agent_id = {
            "claude_code_bridge": "agent_claude_code_bridge_01",
            "openai_compatible_http": "agent_openai_compatible_http_01",
        }.get(adapter_name, f"agent_{adapter_name}_01")

        agent_payload = {
            "adapter_name": adapter_name,
            "agent_id": payload.get("agent_id") or agent_id,
            "input": prompt,
            "message": prompt,
            "text": prompt,
            "prompt": prompt,
            "query": prompt,
            "mode": "codegen",
            "task_type": "codegen",
            "response_format": "code_only",
            "language": language,
            "temperature": float(payload.get("temperature") or 0.2),
        }

        if model:
            agent_payload["model"] = model

        try:
            result = _call(
                "POST",
                "/api/agents/invoke",
                payload=agent_payload,
                timeout=max(int(timeout_seconds or 180), 120),
            )
            last_result = result

            text = _extract_cloud_codegen_text(result, language)
            if text:
                return {
                    "ok": True,
                    "provider": "cloud",
                    "adapter_name": adapter_name,
                    "model": model or "default",
                    "language": language,
                    "text": text,
                    "raw": result,
                }

            debug_path = _write_cloud_codegen_debug(result, f"empty_{adapter_name}")
            if debug_path:
                errors.append(f"{adapter_name}=empty_text(debug={debug_path})")
            else:
                errors.append(f"{adapter_name}=empty_text")
        except Exception as exc:
            errors.append(f"{adapter_name}={exc}")

    if last_result is not None:
        _write_cloud_codegen_debug(last_result, "last_result")

    raise RuntimeError("Cloud codegen returned empty text. " + " | ".join(errors))


def _cloud_codegen(payload: dict, timeout_seconds: int) -> dict:
    payload = _normalize_codegen_payload(payload)

    provider = payload.get("provider") or "ollama"
    fallback_allowed = bool(payload.get("allow_provider_fallback", True))
    errors = []

    if provider == "cloud":
        try:
            return _direct_cloud_codegen(payload, timeout_seconds)
        except Exception as exc:
            errors.append(f"cloud={exc}")
            if not fallback_allowed:
                raise RuntimeError("Cloud codegen failed without fallback: " + str(exc))

            payload["provider"] = "ollama"
            provider = "ollama"

    if provider == "ollama":
        try:
            result = _direct_ollama_codegen(payload, timeout_seconds)
            if errors:
                result["fallback_from"] = errors
            return result
        except Exception as exc:
            errors.append(f"ollama={exc}")
            raise RuntimeError("Ollama codegen failed: " + " | ".join(errors))

    raise HTTPException(
        status_code=400,
        detail={
            "message": f"Unsupported codegen provider: {provider}",
            "supported": ["cloud", "ollama"],
        },
    )
