# -*- coding: utf-8 -*-
"""Tests for app_spec_generator.py (Phase L.2)."""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_SAMPLE_SPEC = {
    "app_name": "TrackFit",
    "tagline": "Your fitness companion",
    "description": "Fitness tracking app with progress visualization",
    "tech_stack": "React + Tailwind CSS + Lucide Icons",
    "features": [
        {"name": "Workout Logger", "description": "Log workouts", "priority": "high"},
        {"name": "Progress Charts", "description": "Visual charts", "priority": "high"},
        {"name": "Goals", "description": "Set weekly goals", "priority": "medium"},
    ],
    "components": ["Header", "Dashboard", "Workout Form"],
    "data_model": [{"entity": "Workout", "fields": ["id", "date", "exercises"]}],
    "pages": [{"name": "Dashboard", "purpose": "Overview"}, {"name": "Log", "purpose": "Add workout"}],
    "styling": {"color_scheme": "dark", "primary_color": "#6366f1", "design_system": "Tailwind CSS"},
    "bolt_diy_prompt": "Build TrackFit: a fitness tracking app...",
    "raw_user_input": "fitness tracker with progress",
}


def _mock_claude(response_dict: dict):
    def fn(prompt, system=None):
        return json.dumps(response_dict, ensure_ascii=False)
    return fn


def test_generate_app_spec_returns_dict():
    from app.services.app_spec_generator import generate_app_spec
    result = generate_app_spec("todo app", _mock_claude(_SAMPLE_SPEC))
    assert isinstance(result, dict)


def test_generate_app_spec_has_required_keys():
    from app.services.app_spec_generator import generate_app_spec
    result = generate_app_spec("todo app", _mock_claude(_SAMPLE_SPEC))
    for key in ("app_name", "tagline", "tech_stack", "features", "bolt_diy_prompt"):
        assert key in result, f"Missing: {key}"


def test_generate_app_spec_preserves_app_name():
    from app.services.app_spec_generator import generate_app_spec
    result = generate_app_spec("test", _mock_claude(_SAMPLE_SPEC))
    assert result["app_name"] == "TrackFit"


def test_generate_app_spec_has_features():
    from app.services.app_spec_generator import generate_app_spec
    result = generate_app_spec("test", _mock_claude(_SAMPLE_SPEC))
    features = result["features"]
    assert len(features) >= 1
    if isinstance(features[0], dict):
        assert "name" in features[0]


def test_generate_app_spec_has_bolt_prompt():
    from app.services.app_spec_generator import generate_app_spec
    result = generate_app_spec("test", _mock_claude(_SAMPLE_SPEC))
    assert len(result["bolt_diy_prompt"]) > 10


def test_generate_app_spec_fallback_on_bad_json():
    from app.services.app_spec_generator import generate_app_spec
    def bad_claude(prompt, system=None):
        return "not json"
    result = generate_app_spec("todo app", bad_claude)
    assert isinstance(result, dict)
    assert "app_name" in result


def test_generate_app_spec_fallback_on_api_error():
    from app.services.app_spec_generator import generate_app_spec
    def error_fn(prompt, system=None):
        raise RuntimeError("API down")
    result = generate_app_spec("app", error_fn)
    assert isinstance(result, dict)
    assert "bolt_diy_prompt" in result


def test_generate_app_spec_strips_markdown_fences():
    from app.services.app_spec_generator import generate_app_spec
    def fn(prompt, system=None):
        return "```json\n" + json.dumps(_SAMPLE_SPEC) + "\n```"
    result = generate_app_spec("test", fn)
    assert result["app_name"] == "TrackFit"


def test_generate_app_spec_stores_raw_input():
    from app.services.app_spec_generator import generate_app_spec
    result = generate_app_spec("my unique input", _mock_claude(_SAMPLE_SPEC))
    assert result.get("raw_user_input") is not None


def test_generate_simple_app_spec_limits_features():
    from app.services.app_spec_generator import generate_simple_app_spec
    result = generate_simple_app_spec("simple app", _mock_claude(_SAMPLE_SPEC))
    features = result.get("features", [])
    assert len(features) <= 3


def test_generate_simple_app_spec_returns_dict():
    from app.services.app_spec_generator import generate_simple_app_spec
    result = generate_simple_app_spec("counter app", _mock_claude(_SAMPLE_SPEC))
    assert isinstance(result, dict)
    assert "app_name" in result


def test_generate_simple_app_spec_has_bolt_prompt():
    from app.services.app_spec_generator import generate_simple_app_spec
    result = generate_simple_app_spec("calculator", _mock_claude(_SAMPLE_SPEC))
    assert len(result.get("bolt_diy_prompt", "")) > 5


def test_generate_simple_fallback_on_error():
    from app.services.app_spec_generator import generate_simple_app_spec
    def error_fn(prompt, system=None):
        raise RuntimeError("fail")
    result = generate_simple_app_spec("app", error_fn)
    assert isinstance(result, dict)


def test_format_spec_preview_returns_string():
    from app.services.app_spec_generator import format_spec_preview
    result = format_spec_preview(_SAMPLE_SPEC)
    assert isinstance(result, str)
    assert "TrackFit" in result


def test_format_spec_preview_contains_features():
    from app.services.app_spec_generator import format_spec_preview
    result = format_spec_preview(_SAMPLE_SPEC)
    assert "Workout Logger" in result or "Фичи" in result


def test_format_spec_preview_empty():
    from app.services.app_spec_generator import format_spec_preview
    result = format_spec_preview({})
    assert isinstance(result, str)
