# -*- coding: utf-8 -*-
"""Guard: register_default_tools wires the full P1 tool set with valid schemas."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.unified.llm_router.tool_registry import ToolRegistry
from app.services.unified.llm_router.tools import register_default_tools


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    register_default_tools(
        reg,
        research_fn=lambda q: {"answer": "x"},
        table_fn=lambda q: {"rows_count": 1, "table_path": "t"},
        image_fn=lambda p, n: {"urls": ["u"]},
        file_qa_fn=lambda c, q: {"answer": "x"},
    )
    return reg


def test_p1_tools_registered():
    names = _registry().names()
    for expected in ("web_research", "build_table", "generate_image", "answer_about_file"):
        assert expected in names


def test_all_tools_have_valid_anthropic_schema():
    tools = _registry().all()
    assert tools, "registry is empty — register_default_tools wired nothing"
    for tool in tools:
        block = tool.to_anthropic()
        assert block["name"]
        assert block["description"]
        assert block["input_schema"]["type"] == "object"
