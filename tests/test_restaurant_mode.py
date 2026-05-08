"""Tests for Phase H3.2: Restaurant Pro."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.restaurant_mode import (
    FOOD_TEMPLATES,
    build_food_prompt,
    generate_dish_photo,
    generate_menu_series,
    generate_social_post,
    list_styles,
)


class TestFoodTemplates:
    def test_all_styles_present(self):
        for style in ("rustic", "modern", "dark", "instagram"):
            assert style in FOOD_TEMPLATES

    def test_templates_not_empty(self):
        for style, tmpl in FOOD_TEMPLATES.items():
            assert len(tmpl) > 20, f"{style} template too short"


class TestBuildFoodPrompt:
    def test_includes_dish_name(self):
        prompt = build_food_prompt("борщ", "rustic")
        assert "борщ" in prompt

    def test_includes_template(self):
        prompt = build_food_prompt("борщ", "rustic")
        assert "rustic" in prompt.lower() or "wooden" in prompt.lower()

    def test_includes_extra_details(self):
        prompt = build_food_prompt("борщ", "rustic", "со сметаной")
        assert "со сметаной" in prompt

    def test_no_extra_details_no_empty_segment(self):
        prompt = build_food_prompt("тарелка борща", "modern", "")
        assert ",," not in prompt
        assert "  " not in prompt

    def test_fallback_to_rustic_for_unknown_style(self):
        prompt = build_food_prompt("салат", "unknown_style_xyz")
        assert "rustic" in prompt.lower() or "wooden" in prompt.lower()

    def test_modern_style_contains_minimalist(self):
        prompt = build_food_prompt("суши", "modern")
        assert "minimalist" in prompt.lower()

    def test_dark_style_contains_moody(self):
        prompt = build_food_prompt("стейк", "dark")
        assert "moody" in prompt.lower() or "dramatic" in prompt.lower()

    def test_instagram_style_contains_instagram(self):
        prompt = build_food_prompt("торт", "instagram")
        assert "instagram" in prompt.lower()


class TestListStyles:
    def test_returns_four_styles(self):
        styles = list_styles()
        assert len(styles) == 4

    def test_each_has_required_keys(self):
        for s in list_styles():
            assert "style" in s
            assert "description" in s
            assert "aspect_ratio" in s

    def test_instagram_aspect_ratio(self):
        styles = {s["style"]: s for s in list_styles()}
        assert styles["instagram"]["aspect_ratio"] == "1:1"


_PATCH_GEN = "app.services.restaurant_mode.generate_images_replicate"
_PATCH_CLAUDE = "app.services.restaurant_mode.call_claude"


class TestGenerateDishPhoto:
    @patch(_PATCH_GEN, return_value=["https://cdn.replicate.com/dish.jpg"])
    def test_returns_url(self, mock_gen):
        url = generate_dish_photo("борщ", style="rustic")
        assert url == "https://cdn.replicate.com/dish.jpg"

    @patch(_PATCH_GEN, return_value=["https://cdn.replicate.com/dish.jpg"])
    def test_passes_correct_num_images(self, mock_gen):
        generate_dish_photo("борщ")
        assert mock_gen.called
        assert mock_gen.call_args[1].get("num_images") == 1

    @patch(_PATCH_GEN, return_value=[])
    def test_returns_empty_on_no_output(self, mock_gen):
        url = generate_dish_photo("борщ")
        assert url == ""

    @patch(_PATCH_GEN, return_value=["https://cdn.replicate.com/dish.jpg"])
    def test_prompt_contains_dish(self, mock_gen):
        generate_dish_photo("пицца маргарита", style="modern")
        prompt_used = mock_gen.call_args[0][0]
        assert "пицца маргарита" in prompt_used

    @patch(_PATCH_GEN, return_value=["https://cdn.replicate.com/dish.jpg"])
    def test_modern_uses_1to1_aspect(self, mock_gen):
        generate_dish_photo("пицца", style="modern")
        assert mock_gen.call_args[1].get("aspect_ratio") == "1:1"


_PATCH_DISH = "app.services.restaurant_mode.generate_dish_photo"


class TestGenerateMenuSeries:
    @patch(_PATCH_DISH, return_value="https://cdn.replicate.com/img.jpg")
    def test_returns_list_for_each_dish(self, mock_gen):
        dishes = ["борщ", "вареники", "котлеты"]
        results = generate_menu_series(dishes, style="modern")
        assert len(results) == 3

    @patch(_PATCH_DISH, return_value="https://cdn.replicate.com/img.jpg")
    def test_each_result_has_keys(self, mock_gen):
        results = generate_menu_series(["борщ"], style="rustic")
        assert "dish" in results[0]
        assert "photo_url" in results[0]
        assert "style" in results[0]

    @patch(_PATCH_DISH, return_value="https://cdn.replicate.com/img.jpg")
    def test_preserves_dish_names(self, mock_gen):
        dishes = ["борщ", "шашлык"]
        results = generate_menu_series(dishes)
        assert results[0]["dish"] == "борщ"
        assert results[1]["dish"] == "шашлык"

    @patch(_PATCH_DISH, return_value="")
    def test_empty_dishes_list(self, mock_gen):
        results = generate_menu_series([])
        assert results == []


class TestGenerateSocialPost:
    @patch(_PATCH_CLAUDE, return_value="Попробуйте наш борщ! 🍲 #restaurant")
    @patch(_PATCH_GEN, return_value=["https://cdn.replicate.com/dish.jpg"])
    def test_returns_required_keys(self, mock_gen, mock_claude):
        result = generate_social_post("борщ")
        assert "photo_url" in result
        assert "caption" in result
        assert "platforms" in result
        assert "best_post_time" in result

    @patch(_PATCH_CLAUDE, return_value="caption")
    @patch(_PATCH_GEN, return_value=["https://cdn.replicate.com/dish.jpg"])
    def test_uses_photo_url(self, mock_gen, mock_claude):
        result = generate_social_post("борщ")
        assert result["photo_url"] == "https://cdn.replicate.com/dish.jpg"

    @patch(_PATCH_CLAUDE, return_value=None)
    @patch(_PATCH_GEN, return_value=["https://cdn.replicate.com/dish.jpg"])
    def test_fallback_caption_when_claude_fails(self, mock_gen, mock_claude):
        result = generate_social_post("борщ")
        assert "борщ" in result["caption"]

    @patch(_PATCH_CLAUDE, return_value="caption")
    @patch(_PATCH_GEN, return_value=["https://cdn.replicate.com/dish.jpg"])
    def test_platforms_include_instagram(self, mock_gen, mock_claude):
        result = generate_social_post("пицца")
        assert "instagram" in result["platforms"]
