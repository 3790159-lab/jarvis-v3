"""Tests for Phase H3.7: Smart Photo Router + Composite Workflows."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.smart_photo_router import (
    analyze_photo_request,
    composite_generate_and_enhance,
    composite_lora_plus_swap,
    estimate_composite_cost,
    format_pipeline_suggestion,
)

_FAKE_URL = "https://cdn.replicate.com/out.jpg"


class TestAnalyzePhotoRequestFood:
    def test_borscht_is_restaurant(self):
        r = analyze_photo_request("сфотографируй борщ")
        assert r["pipeline"] == "restaurant"

    def test_food_keyword(self):
        r = analyze_photo_request("хочу фото еды")
        assert r["pipeline"] == "restaurant"

    def test_menu_keyword(self):
        r = analyze_photo_request("генерация меню ресторана")
        assert r["pipeline"] == "restaurant"

    def test_dish_keyword(self):
        r = analyze_photo_request("create dish photo")
        assert r["pipeline"] == "restaurant"

    def test_restaurant_model(self):
        r = analyze_photo_request("фото блюда пицца")
        assert r["model"] == "flux_pro"

    def test_restaurant_cost(self):
        r = analyze_photo_request("фото блюда")
        assert r["cost"] == pytest.approx(0.04, abs=0.001)


class TestAnalyzePhotoRequestParty:
    def test_party_keyword(self):
        r = analyze_photo_request("создай промо для вечеринки")
        assert r["pipeline"] == "party"

    def test_halloween_is_party(self):
        r = analyze_photo_request("halloween party poster")
        assert r["pipeline"] == "party"

    def test_nye_is_party(self):
        r = analyze_photo_request("nye event promo")
        assert r["pipeline"] == "party"

    def test_birthday_is_party(self):
        r = analyze_photo_request("birthday party invite")
        assert r["pipeline"] == "party"

    def test_party_uses_ultra(self):
        r = analyze_photo_request("party promo poster")
        assert r["model"] == "flux_pro_ultra"


class TestAnalyzePhotoRequestPersonal:
    def test_me_as_is_personal(self):
        r = analyze_photo_request("сделай меня бодибилдером")
        assert r["pipeline"] == "personal"

    def test_me_in_is_personal(self):
        r = analyze_photo_request("generate me in Paris")
        assert r["pipeline"] == "personal"

    def test_needs_lora_when_no_user(self):
        r = analyze_photo_request("me as astronaut")
        assert r["needs_lora"] is True

    @patch("app.services.lora_manager.list_loras",
           return_value=[{"status": "succeeded", "name": "Daniil", "user_id": "alice"}])
    def test_no_lora_needed_when_user_has_lora(self, mock_loras):
        r = analyze_photo_request("me as astronaut", user_id="alice")
        assert r["needs_lora"] is False
        assert r["model"] == "flux_lora"


class TestAnalyzePhotoRequestFaceSwap:
    def test_face_swap_detected(self):
        r = analyze_photo_request("face swap my photo")
        assert r["pipeline"] == "face_swap"

    def test_russian_faceswap(self):
        r = analyze_photo_request("сделай фейсвап")
        assert r["pipeline"] == "face_swap"

    def test_face_swap_cost(self):
        r = analyze_photo_request("face swap")
        assert r["cost"] == pytest.approx(0.005, abs=0.001)


class TestAnalyzePhotoRequestEnhance:
    def test_enhance_with_image(self):
        r = analyze_photo_request("улучши фото", has_image=True)
        assert r["pipeline"] == "enhance"

    def test_enhance_without_image_falls_through(self):
        r = analyze_photo_request("улучши фото", has_image=False)
        assert r["pipeline"] != "enhance"

    def test_enhance_model_is_gfpgan(self):
        r = analyze_photo_request("enhance face", has_image=True)
        assert r["model"] == "gfpgan"


class TestAnalyzePhotoRequestEdit:
    def test_image_without_keywords_is_edit(self):
        r = analyze_photo_request("это интересно", has_image=True)
        assert r["pipeline"] == "edit"

    def test_edit_uses_img2img(self):
        r = analyze_photo_request("transform this", has_image=True)
        assert r["model"] == "img2img"


class TestAnalyzePhotoRequestGeneral:
    def test_no_keywords_is_general(self):
        r = analyze_photo_request("нарисуй звёздное небо")
        assert r["pipeline"] == "general"

    def test_general_uses_ultra(self):
        r = analyze_photo_request("красивый пейзаж")
        assert r["model"] == "flux_pro_ultra"


class TestFormatPipelineSuggestion:
    def test_returns_string(self):
        analysis = analyze_photo_request("фото борща")
        text = format_pipeline_suggestion(analysis)
        assert isinstance(text, str)
        assert len(text) > 20

    def test_contains_cost(self):
        analysis = {"pipeline": "restaurant", "model": "flux_pro",
                    "cost": 0.04, "reason": "test", "needs_lora": False}
        text = format_pipeline_suggestion(analysis)
        assert "0.04" in text or "0.040" in text

    def test_contains_confirm_button(self):
        analysis = {"pipeline": "general", "model": "flux_pro_ultra",
                    "cost": 0.06, "reason": "test", "needs_lora": False}
        text = format_pipeline_suggestion(analysis)
        assert "Подтвердить" in text

    def test_shows_lora_warning_when_needed(self):
        analysis = {"pipeline": "personal", "model": "flux_pro_ultra",
                    "cost": 0.06, "reason": "personal", "needs_lora": True}
        text = format_pipeline_suggestion(analysis)
        assert "lora_train" in text


_PATCH_ROUTER_GEN = "app.services.smart_photo_router.generate_images_replicate"
_PATCH_ROUTER_ENHANCE = "app.services.smart_photo_router.enhance_face"
_PATCH_ROUTER_SWAP = "app.services.smart_photo_router.face_swap_with_polish"
_PATCH_ROUTER_ME = "app.services.smart_photo_router.generate_me_as"


class TestCompositeWorkflows:
    @patch(_PATCH_ROUTER_SWAP, return_value=_FAKE_URL)
    @patch(_PATCH_ROUTER_ME, return_value="https://x.com/lora.jpg")
    def test_composite_lora_plus_swap(self, mock_gen, mock_swap):
        result = composite_lora_plus_swap("alice", "bodybuilder", "https://x.com/face.jpg")
        mock_gen.assert_called_once_with("alice", "bodybuilder")
        mock_swap.assert_called_once_with("https://x.com/face.jpg", "https://x.com/lora.jpg")
        assert result == _FAKE_URL

    @patch(_PATCH_ROUTER_ENHANCE, return_value=_FAKE_URL)
    @patch(_PATCH_ROUTER_GEN, return_value=["https://x.com/gen.jpg"])
    def test_composite_generate_and_enhance(self, mock_gen, mock_enhance):
        result = composite_generate_and_enhance("portrait of a woman")
        mock_gen.assert_called_once()
        mock_enhance.assert_called_once_with("https://x.com/gen.jpg")
        assert result == _FAKE_URL

    @patch(_PATCH_ROUTER_ENHANCE, return_value=_FAKE_URL)
    @patch(_PATCH_ROUTER_GEN, return_value=[])
    def test_composite_generate_empty_returns_empty(self, mock_gen, mock_enhance):
        result = composite_generate_and_enhance("test")
        mock_enhance.assert_not_called()
        assert result == ""

    def test_estimate_composite_cost_lora_swap(self):
        cost = estimate_composite_cost("lora_plus_swap")
        assert cost == pytest.approx(0.037, abs=0.001)

    def test_estimate_composite_cost_generate_enhance(self):
        cost = estimate_composite_cost("generate_and_enhance")
        assert cost == pytest.approx(0.042, abs=0.001)

    def test_estimate_composite_cost_unknown(self):
        assert estimate_composite_cost("no_such_workflow") == 0.0
