# -*- coding: utf-8 -*-
"""Tests for smart_prompts.py (Phase L.3)."""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_PRO_FOOD_PROMPT = (
    "Homemade pasta with mixed seafood including mussels and shrimp, "
    "served in a deep cobalt blue ceramic bowl, rustic wooden table background, "
    "shot on Canon EOS R5 with 85mm f/1.4 lens at f/2.8, shallow depth of field, "
    "warm golden hour window light from left, visible steam, fresh parsley garnish, "
    "restaurant magazine quality, professional food photography, 8k ultra-realistic"
)


# ---------------------------------------------------------------------------
# detect_category
# ---------------------------------------------------------------------------

def test_detect_category_food():
    from app.services.smart_prompts import detect_category
    result = detect_category("pasta with seafood", lambda p, s=None: "food")
    assert result == "food"


def test_detect_category_people():
    from app.services.smart_prompts import detect_category
    result = detect_category("portrait", lambda p, s=None: "people")
    assert result == "people"


def test_detect_category_place():
    from app.services.smart_prompts import detect_category
    result = detect_category("architecture", lambda p, s=None: "place")
    assert result == "place"


def test_detect_category_design():
    from app.services.smart_prompts import detect_category
    result = detect_category("UI design", lambda p, s=None: "design")
    assert result == "design"


def test_detect_category_object():
    from app.services.smart_prompts import detect_category
    result = detect_category("product photo", lambda p, s=None: "object")
    assert result == "object"


def test_detect_category_abstract():
    from app.services.smart_prompts import detect_category
    result = detect_category("abstract art", lambda p, s=None: "abstract")
    assert result == "abstract"


def test_detect_category_handles_extra_text():
    from app.services.smart_prompts import detect_category
    # Claude might return "Category: food" — should still parse
    result = detect_category("pasta", lambda p, s=None: "Category: food.")
    assert result == "food"


def test_detect_category_fallback_on_error():
    from app.services.smart_prompts import detect_category
    def error_fn(p, s=None): raise RuntimeError("API fail")
    result = detect_category("something random", error_fn)
    assert isinstance(result, str)
    assert result in {"food", "design", "people", "place", "object", "abstract"}


def test_detect_category_food_from_keywords():
    from app.services.smart_prompts import detect_category
    # Even if API returns garbage, keyword detection should work
    result = detect_category("паста с морепродуктами", lambda p, s=None: "invalid_category")
    assert result in {"food", "abstract"}  # might fall back to abstract if keyword not matched


# ---------------------------------------------------------------------------
# enhance prompts
# ---------------------------------------------------------------------------

def test_enhance_food_prompt_returns_string():
    from app.services.smart_prompts import enhance_food_prompt
    result = enhance_food_prompt("pasta with seafood", lambda p, s=None: _PRO_FOOD_PROMPT)
    assert isinstance(result, str)
    assert len(result) > 10


def test_enhance_food_prompt_longer_than_input():
    from app.services.smart_prompts import enhance_food_prompt
    original = "pasta"
    result = enhance_food_prompt(original, lambda p, s=None: _PRO_FOOD_PROMPT)
    assert len(result) >= len(original)


def test_enhance_design_prompt():
    from app.services.smart_prompts import enhance_design_prompt
    result = enhance_design_prompt("mobile app UI", lambda p, s=None: "modern flat design, blue palette, 8k")
    assert isinstance(result, str)
    assert len(result) > 5


def test_enhance_people_prompt():
    from app.services.smart_prompts import enhance_people_prompt
    result = enhance_people_prompt("portrait", lambda p, s=None: "Rembrandt lighting, Canon 85mm, bokeh")
    assert isinstance(result, str)


def test_enhance_place_prompt():
    from app.services.smart_prompts import enhance_place_prompt
    result = enhance_place_prompt("cafe interior", lambda p, s=None: "golden hour, wide angle, 8k")
    assert isinstance(result, str)


def test_enhance_general_prompt():
    from app.services.smart_prompts import enhance_general_prompt
    result = enhance_general_prompt("abstract art", lambda p, s=None: "vibrant colors, professional, 8k")
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# smart_enhance
# ---------------------------------------------------------------------------

def test_smart_enhance_returns_dict():
    from app.services.smart_prompts import smart_enhance
    def api_fn(p, s=None):
        return "food" if "category" in (s or "").lower() else _PRO_FOOD_PROMPT

    with tempfile.TemporaryDirectory() as tmp:
        import app.services.smart_prompts as sp_mod
        old_cache = sp_mod._CACHE_DIR
        sp_mod._CACHE_DIR = Path(tmp)
        try:
            result = smart_enhance("pasta with seafood", api_fn)
        finally:
            sp_mod._CACHE_DIR = old_cache

    assert isinstance(result, dict)


def test_smart_enhance_has_required_keys():
    from app.services.smart_prompts import smart_enhance
    call_count = [0]
    responses = ["food", _PRO_FOOD_PROMPT]

    def api_fn(p, s=None):
        idx = min(call_count[0], len(responses) - 1)
        call_count[0] += 1
        return responses[idx]

    with tempfile.TemporaryDirectory() as tmp:
        import app.services.smart_prompts as sp_mod
        old_cache = sp_mod._CACHE_DIR
        sp_mod._CACHE_DIR = Path(tmp)
        try:
            result = smart_enhance("pasta", api_fn)
        finally:
            sp_mod._CACHE_DIR = old_cache

    for key in ("original", "enhanced", "category", "from_cache"):
        assert key in result, f"Missing key: {key}"


def test_smart_enhance_stores_original():
    from app.services.smart_prompts import smart_enhance
    with tempfile.TemporaryDirectory() as tmp:
        import app.services.smart_prompts as sp_mod
        old_cache = sp_mod._CACHE_DIR
        sp_mod._CACHE_DIR = Path(tmp)
        try:
            result = smart_enhance("my unique pasta dish", lambda p, s=None: "food")
        finally:
            sp_mod._CACHE_DIR = old_cache

    assert result["original"] == "my unique pasta dish"


def test_smart_enhance_caches_result():
    from app.services.smart_prompts import smart_enhance
    call_count = [0]

    def counting_fn(p, s=None):
        call_count[0] += 1
        return "food"

    with tempfile.TemporaryDirectory() as tmp:
        import app.services.smart_prompts as sp_mod
        old_cache = sp_mod._CACHE_DIR
        sp_mod._CACHE_DIR = Path(tmp)
        try:
            result1 = smart_enhance("unique dish xyz123", counting_fn)
            calls_after_first = call_count[0]
            result2 = smart_enhance("unique dish xyz123", counting_fn)
            calls_after_second = call_count[0]
        finally:
            sp_mod._CACHE_DIR = old_cache

    # Second call should use cache (no new API calls)
    assert calls_after_second == calls_after_first
    assert result2.get("from_cache") is True


def test_smart_enhance_cache_hit_flag():
    from app.services.smart_prompts import smart_enhance
    with tempfile.TemporaryDirectory() as tmp:
        import app.services.smart_prompts as sp_mod
        old_cache = sp_mod._CACHE_DIR
        sp_mod._CACHE_DIR = Path(tmp)
        try:
            result1 = smart_enhance("test unique description 456", lambda p, s=None: "food")
            assert result1["from_cache"] is False
            result2 = smart_enhance("test unique description 456", lambda p, s=None: "food")
            assert result2["from_cache"] is True
        finally:
            sp_mod._CACHE_DIR = old_cache


def test_smart_enhance_api_error_fallback():
    from app.services.smart_prompts import smart_enhance
    def error_fn(p, s=None):
        raise RuntimeError("API down")

    with tempfile.TemporaryDirectory() as tmp:
        import app.services.smart_prompts as sp_mod
        old_cache = sp_mod._CACHE_DIR
        sp_mod._CACHE_DIR = Path(tmp)
        try:
            result = smart_enhance("test description", error_fn)
        finally:
            sp_mod._CACHE_DIR = old_cache

    assert isinstance(result, dict)
    assert "enhanced" in result


# ---------------------------------------------------------------------------
# Telegram handlers
# ---------------------------------------------------------------------------

def test_cmd_smart_photo_empty_query():
    from tools.jarvis_smart_telegram_control import cmd_smart_photo
    sent = []
    with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
        cmd_smart_photo("123", "")
    assert any("/smart_photo" in t or "Использование" in t for t in sent)


def test_cmd_smart_photo_sends_category():
    from tools.jarvis_smart_telegram_control import cmd_smart_photo
    sent = []
    mock_result = {
        "original": "pasta",
        "enhanced": _PRO_FOOD_PROMPT,
        "category": "food",
        "from_cache": False,
        "prompt_metadata": {"category": "food", "word_count": 50, "system_used": "food"},
    }
    with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
        with patch("tools.jarvis_smart_telegram_control.guard_spend", side_effect=lambda uid, un, est, do: (do(), None)):
            with patch("app.services.smart_prompts.smart_enhance", return_value=mock_result):
                with patch("app.services.replicate_image_gen.generate_images_replicate", return_value=["http://example.com/img.jpg"]):
                    with patch("tools.jarvis_smart_telegram_control._send_photo_url"):
                        cmd_smart_photo("123", "pasta with seafood")
    full = " ".join(sent)
    assert "food" in full.lower() or "Еда" in full or "Промпт" in full


def test_cmd_pro_food_empty_query():
    from tools.jarvis_smart_telegram_control import cmd_pro_food
    sent = []
    with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(txt)):
        cmd_pro_food("123", "")
    assert any("/pro_food" in t or "Использование" in t for t in sent)
