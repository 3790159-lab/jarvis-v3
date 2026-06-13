# -*- coding: utf-8 -*-
"""Unified LLM router package — Claude tool_use orchestrator for Jarvis.

Public surface:

- :class:`~app.services.unified.llm_router.router.LLMRouter` — the orchestrator.
- :class:`~app.services.unified.llm_router.router.RouterResponse` — its result.
- :class:`~app.services.unified.llm_router.tool_registry.Tool`,
  :class:`ToolRegistry`, :class:`ToolContext`, :class:`ToolResult` — the tool
  contract.
"""
from app.services.unified.llm_router.llm_client import (
    DEFAULT_MODEL,
    build_anthropic_client,
    compute_cost,
    resolve_model,
)
from app.services.unified.llm_router.router import (
    LLMRouter,
    RouterResponse,
)
from app.services.unified.llm_router.tool_registry import (
    Tool,
    ToolContext,
    ToolRegistry,
    ToolResult,
    get_default_registry,
)

__all__ = [
    "LLMRouter",
    "RouterResponse",
    "compute_cost",
    "DEFAULT_MODEL",
    "build_anthropic_client",
    "resolve_model",
    "Tool",
    "ToolContext",
    "ToolRegistry",
    "ToolResult",
    "get_default_registry",
]
