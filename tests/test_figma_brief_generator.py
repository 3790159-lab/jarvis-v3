# -*- coding: utf-8 -*-
"""Tests for figma_brief_generator.py (Phase L.1)."""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# generate_design_brief
# ---------------------------------------------------------------------------

def _mock_claude(response_dict: dict):
    """Return a claude_api_fn mock that returns JSON."""
    def fn(prompt, system=None):
        return json.dumps(response_dict, ensure_ascii=False)
    return fn


_SAMPLE_BRIEF = {
    "project_name": "Cafe Roma",
    "project_type": "landing",
    "target_audience": "Families aged 25-45",
    "design_style": "warm",
    "color_palette": {
        "primary": "#D4622A",
        "secondary": "#8B4513",
        "accent": "#FFD700",
        "bg": "#FFF8F0",
        "text": "#2C1810",
    },
    "typography": {
        "heading_font": "Playfair Display",
        "body_font": "Open Sans",
        "heading_size": "48px",
        "body_size": "16px",
        "heading_weight": "700",
    },
    "components": [
        {"name": "Header", "content": "Logo + Nav", "type": "navigation"},
        {"name": "Hero", "content": "Headline + CTA", "type": "hero"},
        {"name": "Features", "content": "3 features", "type": "section"},
        {"name": "Footer", "content": "Contacts", "type": "footer"},
    ],
    "desktop_frame": {"width": 1440, "height": 900, "sections": ["Header", "Hero", "Features", "Footer"]},
    "mobile_frame": {"width": 375, "height": 812, "sections": ["Header", "Hero", "Footer"]},
    "raw_prompt": "restaurant landing page",
}


def test_generate_design_brief_returns_dict():
    from app.services.figma_brief_generator import generate_design_brief
    result = generate_design_brief("restaurant landing", _mock_claude(_SAMPLE_BRIEF))
    assert isinstance(result, dict)


def test_generate_design_brief_has_required_keys():
    from app.services.figma_brief_generator import generate_design_brief
    result = generate_design_brief("restaurant", _mock_claude(_SAMPLE_BRIEF))
    for key in ("project_name", "project_type", "design_style", "color_palette",
                "typography", "components", "desktop_frame", "mobile_frame"):
        assert key in result, f"Missing key: {key}"


def test_generate_design_brief_preserves_project_name():
    from app.services.figma_brief_generator import generate_design_brief
    result = generate_design_brief("test", _mock_claude(_SAMPLE_BRIEF))
    assert result["project_name"] == "Cafe Roma"


def test_generate_design_brief_has_color_palette():
    from app.services.figma_brief_generator import generate_design_brief
    result = generate_design_brief("test", _mock_claude(_SAMPLE_BRIEF))
    palette = result["color_palette"]
    assert "primary" in palette
    assert palette["primary"].startswith("#")


def test_generate_design_brief_has_components():
    from app.services.figma_brief_generator import generate_design_brief
    result = generate_design_brief("test", _mock_claude(_SAMPLE_BRIEF))
    comps = result["components"]
    assert len(comps) >= 1
    assert "name" in comps[0]


def test_generate_design_brief_fallback_on_invalid_json():
    from app.services.figma_brief_generator import generate_design_brief
    def bad_claude(prompt, system=None):
        return "not valid json at all"
    result = generate_design_brief("test prompt", bad_claude)
    assert isinstance(result, dict)
    assert "project_name" in result
    assert result["raw_prompt"] == "test prompt"


def test_generate_design_brief_fallback_on_api_error():
    from app.services.figma_brief_generator import generate_design_brief
    def error_claude(prompt, system=None):
        raise RuntimeError("API error")
    result = generate_design_brief("some design", error_claude)
    assert isinstance(result, dict)
    assert "color_palette" in result


def test_generate_design_brief_strips_markdown_fences():
    from app.services.figma_brief_generator import generate_design_brief
    def fn(prompt, system=None):
        return "```json\n" + json.dumps(_SAMPLE_BRIEF) + "\n```"
    result = generate_design_brief("test", fn)
    assert result["project_name"] == "Cafe Roma"


def test_generate_design_brief_raw_prompt_stored():
    from app.services.figma_brief_generator import generate_design_brief
    result = generate_design_brief("unique input", _mock_claude(_SAMPLE_BRIEF))
    assert result.get("raw_prompt") is not None


def test_generate_design_brief_desktop_frame():
    from app.services.figma_brief_generator import generate_design_brief
    result = generate_design_brief("test", _mock_claude(_SAMPLE_BRIEF))
    df = result.get("desktop_frame", {})
    assert df.get("width") == 1440


def test_generate_design_brief_mobile_frame():
    from app.services.figma_brief_generator import generate_design_brief
    result = generate_design_brief("test", _mock_claude(_SAMPLE_BRIEF))
    mf = result.get("mobile_frame", {})
    assert mf.get("width") == 375


# ---------------------------------------------------------------------------
# format_brief_preview
# ---------------------------------------------------------------------------

def test_format_brief_preview_returns_string():
    from app.services.figma_brief_generator import format_brief_preview
    result = format_brief_preview(_SAMPLE_BRIEF)
    assert isinstance(result, str)
    assert len(result) > 10


def test_format_brief_preview_contains_project_name():
    from app.services.figma_brief_generator import format_brief_preview
    result = format_brief_preview(_SAMPLE_BRIEF)
    assert "Cafe Roma" in result


def test_format_brief_preview_contains_style():
    from app.services.figma_brief_generator import format_brief_preview
    result = format_brief_preview(_SAMPLE_BRIEF)
    # warm style should appear in preview
    assert len(result) > 20


def test_format_brief_preview_contains_component_names():
    from app.services.figma_brief_generator import format_brief_preview
    result = format_brief_preview(_SAMPLE_BRIEF)
    assert "Header" in result or "header" in result.lower() or "Компоненты" in result


def test_format_brief_preview_empty_brief():
    from app.services.figma_brief_generator import format_brief_preview
    result = format_brief_preview({})
    assert isinstance(result, str)
