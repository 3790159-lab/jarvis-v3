from __future__ import annotations

from typing import Any, Dict, List


def get_tool_registry() -> List[Dict[str, Any]]:
    return [
        {
            "name": "shell",
            "enabled": True,
            "description": "Run a restricted local shell command",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout_seconds": {"type": "integer"},
                    "working_directory": {"type": "string"},
                },
                "required": ["command"],
            },
        },
        {
            "name": "python",
            "enabled": True,
            "description": "Run restricted local python code",
            "input_schema": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "timeout_seconds": {"type": "integer"},
                },
                "required": ["code"],
            },
        },
        {
            "name": "http",
            "enabled": True,
            "description": "Run outbound HTTP requests",
            "input_schema": {
                "type": "object",
                "properties": {
                    "method": {"type": "string"},
                    "url": {"type": "string"},
                    "headers": {"type": "object"},
                    "json": {"type": "object"},
                    "timeout_seconds": {"type": "integer"},
                },
                "required": ["method", "url"],
            },
        },
        {
            "name": "file_write",
            "enabled": True,
            "description": "Write a text file inside approved runtime directories",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
        {
            "name": "file_read",
            "enabled": True,
            "description": "Read a text file inside approved runtime directories",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
        },
    ]


def get_tool_names() -> List[str]:
    return [item["name"] for item in get_tool_registry() if item.get("enabled")]

def get_tool_spec(tool_name: str):
    """
    Compatibility helper for older routes that expect get_tool_spec().
    """
    registry_candidates = []

    for candidate_name in ("TOOL_REGISTRY", "tool_registry", "REGISTRY", "registry", "_REGISTRY", "_tool_registry"):
        if candidate_name in globals():
            registry_candidates.append(globals()[candidate_name])

    getter = globals().get("get_tool_registry")
    if callable(getter):
        try:
            registry_candidates.append(getter())
        except Exception:
            pass

    for registry in registry_candidates:
        try:
            if isinstance(registry, dict):
                if tool_name in registry:
                    return registry[tool_name]
                tools = registry.get("tools")
                if isinstance(tools, dict) and tool_name in tools:
                    return tools[tool_name]
            if hasattr(registry, "get"):
                spec = registry.get(tool_name)
                if spec is not None:
                    return spec
        except Exception:
            pass

    return {
        "name": tool_name,
        "status": "compat_fallback",
        "available": False,
        "description": f"Fallback spec generated for {tool_name}",
    }
