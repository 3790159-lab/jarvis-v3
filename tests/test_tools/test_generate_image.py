# -*- coding: utf-8 -*-
"""Unit tests for the generate_image router tool (wraps /image/generate, Replicate FLUX)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.unified.llm_router.tool_registry import ToolContext
from app.services.unified.llm_router.tools.generate_image import build_generate_image_tool


def _ctx() -> ToolContext:
    return ToolContext(user_id=1, username="u", chat_id="1")


def test_schema_requires_prompt():
    tool = build_generate_image_tool(image_fn=lambda p, n: {"urls": ["u"]})
    assert tool.name == "generate_image"
    block = tool.to_anthropic()
    assert block["input_schema"]["required"] == ["prompt"]


def test_returns_photo_with_first_url():
    seen = []

    def fake(prompt, num_images):
        seen.append((prompt, num_images))
        return {"urls": ["https://img/1.png", "https://img/2.png"], "provider": "replicate"}

    tool = build_generate_image_tool(image_fn=fake)
    result = asyncio.run(tool.handler({"prompt": "кот в очках"}, _ctx()))
    assert seen == [("кот в очках", 1)]
    assert result.kind == "photo"
    assert result.media == "https://img/1.png"


def test_num_images_clamped_to_4():
    seen = []
    tool = build_generate_image_tool(image_fn=lambda p, n: seen.append(n) or {"urls": ["u"]})
    asyncio.run(tool.handler({"prompt": "x", "num_images": 99}, _ctx()))
    assert seen == [4]


def test_empty_urls_is_failure():
    tool = build_generate_image_tool(image_fn=lambda p, n: {"urls": []})
    result = asyncio.run(tool.handler({"prompt": "x"}, _ctx()))
    assert result.is_error


def test_backend_error_becomes_failure():
    tool = build_generate_image_tool(image_fn=lambda p, n: {"_error": "REPLICATE_API_KEY missing"})
    result = asyncio.run(tool.handler({"prompt": "x"}, _ctx()))
    assert result.is_error
    assert "REPLICATE" in result.error


def test_empty_prompt_fails_without_backend():
    called = []
    tool = build_generate_image_tool(image_fn=lambda p, n: called.append(p) or {"urls": ["u"]})
    result = asyncio.run(tool.handler({"prompt": "   "}, _ctx()))
    assert result.is_error
    assert called == []


def test_unwired_backend_degrades():
    tool = build_generate_image_tool(image_fn=None)
    result = asyncio.run(tool.handler({"prompt": "x"}, _ctx()))
    assert result.is_error


def test_exception_is_caught():
    def boom(p, n):
        raise RuntimeError("provider down")

    tool = build_generate_image_tool(image_fn=boom)
    result = asyncio.run(tool.handler({"prompt": "x"}, _ctx()))
    assert result.is_error
    assert "provider down" in result.error
