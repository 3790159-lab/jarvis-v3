# -*- coding: utf-8 -*-
"""Tool implementations for the unified LLM router.

Each module exposes a ``build_*_tool(...)`` factory returning a
:class:`~app.services.unified.llm_router.tool_registry.Tool`. Backends
(generation, dispatch, stats) are injected so the tools are unit-testable
without touching FLUX, Telegram, or the cost ledger.

:func:`register_default_tools` wires the Step-1 tool set into a registry; the
bot bridge calls it during startup.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from app.services.unified.llm_router.tool_registry import ToolRegistry
from app.services.unified.llm_router.tools.answer_about_file import (
    build_answer_about_file_tool,
)
from app.services.unified.llm_router.tools.build_table import build_table_tool
from app.services.unified.llm_router.tools.cost_stats import build_cost_stats_tool
from app.services.unified.llm_router.tools.generate_image import (
    build_generate_image_tool,
)
from app.services.unified.llm_router.tools.persona_photo import (
    build_persona_photo_tool,
)
from app.services.unified.llm_router.tools.swap_batch import build_swap_batch_tools
from app.services.unified.llm_router.tools.video_face_swap import (
    build_video_face_swap_tool,
)
from app.services.unified.llm_router.tools.voice_reply import build_voice_reply_tool
from app.services.unified.llm_router.tools.web_research import build_web_research_tool


def register_default_tools(
    registry: ToolRegistry,
    *,
    dispatch_fn: Optional[Callable[..., Any]] = None,
    set_quality_fn: Optional[Callable[..., Any]] = None,
    persona_generate_fn: Optional[Callable[..., Any]] = None,
    persona_exists_fn: Optional[Callable[..., Any]] = None,
    stats_fn: Optional[Callable[..., Any]] = None,
    research_fn: Optional[Callable[..., Any]] = None,
    table_fn: Optional[Callable[..., Any]] = None,
    image_fn: Optional[Callable[..., Any]] = None,
    video_swap_dispatch_fn: Optional[Callable[..., Any]] = None,
    voice_synthesize_fn: Optional[Callable[..., Any]] = None,
    voice_send_fn: Optional[Callable[..., Any]] = None,
    file_qa_fn: Optional[Callable[..., Any]] = None,
) -> ToolRegistry:
    """Register the default tool set (persona photo, swap batch, video swap, voice, stats)."""
    registry.register(
        build_persona_photo_tool(
            generate_fn=persona_generate_fn, persona_exists_fn=persona_exists_fn
        )
    )
    for tool in build_swap_batch_tools(
        dispatch_fn=dispatch_fn, set_quality_fn=set_quality_fn
    ):
        registry.register(tool)
    registry.register(
        build_video_face_swap_tool(dispatch_fn=video_swap_dispatch_fn)
    )
    registry.register(
        build_voice_reply_tool(
            synthesize_fn=voice_synthesize_fn, send_fn=voice_send_fn
        )
    )
    registry.register(build_cost_stats_tool(stats_fn=stats_fn))
    registry.register(build_web_research_tool(research_fn=research_fn))
    registry.register(build_table_tool(table_fn=table_fn))
    registry.register(build_generate_image_tool(image_fn=image_fn))
    registry.register(build_answer_about_file_tool(file_qa_fn=file_qa_fn))
    return registry


__all__ = [
    "register_default_tools",
    "build_persona_photo_tool",
    "build_swap_batch_tools",
    "build_video_face_swap_tool",
    "build_voice_reply_tool",
    "build_cost_stats_tool",
    "build_web_research_tool",
    "build_table_tool",
    "build_generate_image_tool",
    "build_answer_about_file_tool",
]
