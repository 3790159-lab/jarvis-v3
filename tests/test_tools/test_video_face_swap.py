# -*- coding: utf-8 -*-
"""Tests for the ``video_face_swap`` router tool."""
from __future__ import annotations

import asyncio

from app.services.unified.llm_router.tool_registry import ToolContext, ToolRegistry
from app.services.unified.llm_router.tools import register_default_tools
from app.services.unified.llm_router.tools.video_face_swap import (
    build_video_face_swap_tool,
)


def _ctx() -> ToolContext:
    return ToolContext(user_id=1, username="u", chat_id="555", conversation_state={})


def test_tool_name_and_rich_description():
    tool = build_video_face_swap_tool()
    assert tool.name == "video_face_swap"
    assert tool.description and len(tool.description) >= 60
    low = tool.description.lower()
    assert any(cue in low for cue in ("когда", "например", "пример"))
    assert "видео" in low


def test_handler_without_dispatch_fails_gracefully():
    tool = build_video_face_swap_tool(dispatch_fn=None)
    result = asyncio.run(tool.handler({}, _ctx()))
    assert result.is_error


def test_handler_with_dispatch_invokes_it_and_confirms():
    calls = []
    tool = build_video_face_swap_tool(dispatch_fn=lambda chat: calls.append(chat))
    result = asyncio.run(tool.handler({}, _ctx()))
    assert not result.is_error
    assert calls == [555]  # chat_id coerced to int


def test_registered_in_default_tools_when_wired():
    reg = ToolRegistry()
    register_default_tools(
        reg,
        dispatch_fn=lambda *a, **k: None,
        set_quality_fn=lambda *a, **k: None,
        stats_fn=lambda *a, **k: "s",
        video_swap_dispatch_fn=lambda *a, **k: None,
    )
    assert reg.get("video_face_swap") is not None
