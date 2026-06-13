# -*- coding: utf-8 -*-
"""The future-phase stub tools are valid Tools that fail cleanly (not-yet-built).

These skeletons exist so Steps 2+ (Computer Use, Office files, live face-swap)
have a registered shape to flesh out. Until then their handlers must return a
graceful error rather than raise.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.unified.llm_router.tool_registry import ToolContext
from app.services.unified.llm_router.tools.stubs import build_stub_tools


def _ctx() -> ToolContext:
    return ToolContext(user_id=1, username="u", chat_id="1")


def test_stub_tools_have_valid_schemas():
    tools = build_stub_tools()
    assert len(tools) >= 3
    names = {t.name for t in tools}
    # The Phase B/C/D placeholders the unified build will grow into.
    assert {"computer_use", "office_file", "live_face_swap"} <= names
    for t in tools:
        block = t.to_anthropic()
        assert block["name"] == t.name
        assert block["input_schema"]["type"] == "object"
        assert t.description


def test_stub_tools_fail_gracefully():
    for t in build_stub_tools():
        result = asyncio.run(t.handler({}, _ctx()))
        assert result.is_error
        assert "Phase" in result.error or "не реализован" in result.error.lower()
