"""Tests for Phase H3.6: Personal Generation Mode."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.personal_mode import (
    PLACES_TEMPLATES,
    ROLES_TEMPLATES,
    STYLE_TEMPLATES,
    generate_me_as,
    generate_me_in,
    generate_me_in_style,
    list_places,
    list_roles,
    list_styles,
)

_PATCH_LIST_LORAS = "app.services.personal_mode.list_loras"
_PATCH_GEN_LORA = "app.services.personal_mode.generate_with_lora"
_READY_LORA = [{"name": "Daniil", "status": "succeeded", "trigger_word": "DANIIL", "user_id": "alice"}]
_FAKE_URL = "https://cdn.replicate.com/personal.jpg"


class TestTemplates:
    def test_roles_not_empty(self):
        assert len(ROLES_TEMPLATES) >= 8

    def test_places_not_empty(self):
        assert len(PLACES_TEMPLATES) >= 6

    def test_styles_not_empty(self):
        assert len(STYLE_TEMPLATES) >= 6

    def test_all_role_values_are_strings(self):
        for name, val in ROLES_TEMPLATES.items():
            assert isinstance(val, str) and len(val) > 10, f"{name} too short"

    def test_all_place_values_are_strings(self):
        for name, val in PLACES_TEMPLATES.items():
            assert isinstance(val, str) and len(val) > 5

    def test_all_style_values_are_strings(self):
        for name, val in STYLE_TEMPLATES.items():
            assert isinstance(val, str) and len(val) > 5


class TestListFunctions:
    def test_list_roles_count(self):
        assert len(list_roles()) >= 8

    def test_list_roles_structure(self):
        for r in list_roles():
            assert "role" in r
            assert "description" in r

    def test_list_places_count(self):
        assert len(list_places()) >= 6

    def test_list_places_structure(self):
        for p in list_places():
            assert "place" in p
            assert "description" in p

    def test_list_styles_count(self):
        assert len(list_styles()) >= 6

    def test_list_styles_structure(self):
        for s in list_styles():
            assert "style" in s
            assert "description" in s


class TestGenerateMeAs:
    @patch(_PATCH_GEN_LORA, return_value=_FAKE_URL)
    @patch(_PATCH_LIST_LORAS, return_value=_READY_LORA)
    def test_returns_url(self, mock_list, mock_gen):
        result = generate_me_as("alice", "bodybuilder")
        assert result == _FAKE_URL

    @patch(_PATCH_GEN_LORA, return_value=_FAKE_URL)
    @patch(_PATCH_LIST_LORAS, return_value=_READY_LORA)
    def test_passes_role_prompt(self, mock_list, mock_gen):
        generate_me_as("alice", "chef")
        prompt = mock_gen.call_args[0][1]
        assert "chef" in prompt.lower()

    @patch(_PATCH_GEN_LORA, return_value=_FAKE_URL)
    @patch(_PATCH_LIST_LORAS, return_value=_READY_LORA)
    def test_unknown_role_uses_raw_string(self, mock_list, mock_gen):
        generate_me_as("alice", "pirate king")
        prompt = mock_gen.call_args[0][1]
        assert "pirate king" in prompt

    @patch(_PATCH_GEN_LORA, return_value=_FAKE_URL)
    @patch(_PATCH_LIST_LORAS, return_value=_READY_LORA)
    def test_extra_details_in_prompt(self, mock_list, mock_gen):
        generate_me_as("alice", "bodybuilder", extra_details="holding gold trophy")
        prompt = mock_gen.call_args[0][1]
        assert "holding gold trophy" in prompt

    @patch(_PATCH_LIST_LORAS, return_value=[])
    def test_raises_when_no_lora(self, mock_list):
        with pytest.raises(ValueError, match="lora_train"):
            generate_me_as("alice", "bodybuilder")

    @patch(_PATCH_LIST_LORAS, return_value=[{"name": "m", "status": "training", "user_id": "alice"}])
    def test_raises_when_lora_not_ready(self, mock_list):
        with pytest.raises(ValueError, match="lora_train"):
            generate_me_as("alice", "model")


class TestGenerateMeIn:
    @patch(_PATCH_GEN_LORA, return_value=_FAKE_URL)
    @patch(_PATCH_LIST_LORAS, return_value=_READY_LORA)
    def test_returns_url(self, mock_list, mock_gen):
        result = generate_me_in("alice", "paris")
        assert result == _FAKE_URL

    @patch(_PATCH_GEN_LORA, return_value=_FAKE_URL)
    @patch(_PATCH_LIST_LORAS, return_value=_READY_LORA)
    def test_prompt_contains_place_content(self, mock_list, mock_gen):
        generate_me_in("alice", "maldives")
        prompt = mock_gen.call_args[0][1]
        assert "maldives" in prompt.lower() or "bungalow" in prompt.lower()

    @patch(_PATCH_GEN_LORA, return_value=_FAKE_URL)
    @patch(_PATCH_LIST_LORAS, return_value=_READY_LORA)
    def test_unknown_place_uses_raw(self, mock_list, mock_gen):
        generate_me_in("alice", "mars colony")
        prompt = mock_gen.call_args[0][1]
        assert "mars colony" in prompt

    @patch(_PATCH_LIST_LORAS, return_value=[])
    def test_raises_when_no_lora(self, mock_list):
        with pytest.raises(ValueError):
            generate_me_in("alice", "paris")


class TestGenerateMeInStyle:
    @patch(_PATCH_GEN_LORA, return_value=_FAKE_URL)
    @patch(_PATCH_LIST_LORAS, return_value=_READY_LORA)
    def test_returns_url(self, mock_list, mock_gen):
        result = generate_me_in_style("alice", "cyberpunk")
        assert result == _FAKE_URL

    @patch(_PATCH_GEN_LORA, return_value=_FAKE_URL)
    @patch(_PATCH_LIST_LORAS, return_value=_READY_LORA)
    def test_prompt_contains_style(self, mock_list, mock_gen):
        generate_me_in_style("alice", "noir")
        prompt = mock_gen.call_args[0][1]
        assert "noir" in prompt.lower()

    @patch(_PATCH_GEN_LORA, return_value=_FAKE_URL)
    @patch(_PATCH_LIST_LORAS, return_value=_READY_LORA)
    def test_extra_details_included(self, mock_list, mock_gen):
        generate_me_in_style("alice", "vintage", extra_details="sitting in Cadillac")
        prompt = mock_gen.call_args[0][1]
        assert "sitting in Cadillac" in prompt

    @patch(_PATCH_LIST_LORAS, return_value=[])
    def test_raises_when_no_lora(self, mock_list):
        with pytest.raises(ValueError):
            generate_me_in_style("alice", "cyberpunk")
