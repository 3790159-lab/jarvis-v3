# -*- coding: utf-8 -*-
"""Unit tests for the ``generate_persona_photo`` tool.

The tool's image-generation backend is injectable so these tests never touch
FLUX / Replicate. We verify the Anthropic schema is well-formed, a successful
generation yields a photo :class:`ToolResult`, and an unknown persona fails
gracefully (no exception escapes the handler).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.unified.llm_router.tool_registry import ToolContext
from app.services.unified.llm_router.tools.persona_photo import (
    build_persona_photo_tool,
)


def _ctx() -> ToolContext:
    return ToolContext(user_id=222, username="vasya", chat_id="222")


def test_persona_photo_tool_schema_valid():
    tool = build_persona_photo_tool()

    assert tool.name == "generate_persona_photo"
    assert tool.description and isinstance(tool.description, str)

    schema = tool.input_schema
    assert schema["type"] == "object"
    assert "persona_id" in schema["properties"]
    assert "prompt" in schema["properties"]
    assert "count" in schema["properties"]
    assert "persona_id" in schema["required"]

    # Round-trips into the Messages API tool block shape.
    block = tool.to_anthropic()
    assert block["name"] == "generate_persona_photo"
    assert block["input_schema"] is schema


def test_persona_photo_tool_executes():
    seen: list = []

    def fake_generate(persona_id, prompt, count):
        seen.append((persona_id, prompt, count))
        return ["https://img.example/persona1.png"]

    tool = build_persona_photo_tool(
        generate_fn=fake_generate,
        persona_exists_fn=lambda pid: True,
    )

    result = asyncio.run(
        tool.handler(
            {"persona_id": "alice", "prompt": "на пляже", "count": 1}, _ctx()
        )
    )

    assert result.kind == "photo"
    assert result.media == "https://img.example/persona1.png"
    assert not result.is_error
    assert seen == [("alice", "на пляже", 1)]


def test_persona_photo_tool_handles_unknown_persona():
    def fake_generate(persona_id, prompt, count):  # pragma: no cover - must not run
        raise AssertionError("generation should not be attempted for unknown persona")

    tool = build_persona_photo_tool(
        generate_fn=fake_generate,
        persona_exists_fn=lambda pid: False,
    )

    result = asyncio.run(
        tool.handler({"persona_id": "ghost", "prompt": "x"}, _ctx())
    )

    assert result.is_error
    assert "ghost" in result.error
