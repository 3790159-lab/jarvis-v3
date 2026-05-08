"""Phase I.2: Russian dishes translation for accurate FLUX generation."""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_borscht_translates_to_english():
    from app.services.restaurant_mode import _translate_dish_name
    result = _translate_dish_name("борщ")
    assert "borscht" in result.lower()
    assert "борщ" not in result


def test_unknown_dish_kept_as_is():
    from app.services.restaurant_mode import _translate_dish_name
    result = _translate_dish_name("pizza margherita")
    assert result == "pizza margherita"


def test_translation_case_insensitive():
    from app.services.restaurant_mode import _translate_dish_name
    assert _translate_dish_name("БОРЩ") == _translate_dish_name("борщ")
    assert _translate_dish_name("Пельмени") == _translate_dish_name("пельмени")


def test_translation_includes_cultural_context():
    from app.services.restaurant_mode import _translate_dish_name
    result = _translate_dish_name("пельмени")
    assert "Russian" in result or "dumpling" in result.lower()


def test_build_food_prompt_uses_translated_name():
    from app.services.restaurant_mode import build_food_prompt
    prompt = build_food_prompt("борщ", style="rustic")
    assert "borscht" in prompt.lower()
    assert "борщ" not in prompt


def test_build_food_prompt_english_dish_unchanged():
    from app.services.restaurant_mode import build_food_prompt
    prompt = build_food_prompt("caesar salad", style="modern")
    assert "caesar salad" in prompt.lower()


def test_all_dict_entries_are_english():
    from app.services.restaurant_mode import RUSSIAN_DISHES_EN
    for ru, en in RUSSIAN_DISHES_EN.items():
        assert en, f"Empty translation for {ru}"
        assert len(en) > 5, f"Translation too short for {ru}: {en}"
