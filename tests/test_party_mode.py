"""Tests for Phase H3.3: Party Pro."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.party_mode import (
    PARTY_THEMES,
    build_party_prompt,
    generate_event_photo,
    generate_invite_card,
    generate_party_promo,
    list_themes,
)

_PATCH_GEN = "app.services.party_mode.generate_images_replicate"
_PATCH_CLAUDE = "app.services.party_mode.call_claude"
_FAKE_URL = "https://cdn.replicate.com/party.jpg"


class TestPartyThemes:
    def test_all_core_themes_present(self):
        for theme in ("nye", "halloween", "birthday", "wedding", "summer", "corporate"):
            assert theme in PARTY_THEMES

    def test_themes_not_empty(self):
        for name, desc in PARTY_THEMES.items():
            assert len(desc) > 20, f"{name} description too short"

    def test_list_themes_count(self):
        assert len(list_themes()) >= 6

    def test_list_themes_structure(self):
        for t in list_themes():
            assert "theme" in t
            assert "description" in t


class TestBuildPartyPrompt:
    def test_includes_theme_content(self):
        prompt = build_party_prompt("nye")
        assert "champagne" in prompt.lower() or "gold" in prompt.lower()

    def test_includes_extra(self):
        prompt = build_party_prompt("birthday", extra="outdoor garden")
        assert "outdoor garden" in prompt

    def test_no_double_comma_when_no_extra(self):
        prompt = build_party_prompt("halloween", extra="")
        assert ",," not in prompt

    def test_unknown_theme_uses_raw_string(self):
        prompt = build_party_prompt("cyberpunk_rave")
        assert "cyberpunk_rave" in prompt

    def test_poster_flag_adds_poster_suffix(self):
        prompt = build_party_prompt("birthday", poster=True)
        assert "poster" in prompt.lower()

    def test_non_poster_flag_changes_suffix(self):
        prompt_poster = build_party_prompt("birthday", poster=True)
        prompt_card = build_party_prompt("birthday", poster=False)
        assert prompt_poster != prompt_card


class TestGeneratePartyPromo:
    @patch(_PATCH_CLAUDE, return_value="🎉 NYE Party\n📅 31 Dec\n✨ Magic night!")
    @patch(_PATCH_GEN, return_value=[_FAKE_URL])
    def test_returns_required_keys(self, mock_gen, mock_claude):
        result = generate_party_promo("nye")
        assert "poster_url" in result
        assert "promo_text" in result
        assert "theme" in result

    @patch(_PATCH_CLAUDE, return_value="promo")
    @patch(_PATCH_GEN, return_value=[_FAKE_URL])
    def test_poster_url_is_replicate_result(self, mock_gen, mock_claude):
        result = generate_party_promo("birthday")
        assert result["poster_url"] == _FAKE_URL

    @patch(_PATCH_CLAUDE, return_value=None)
    @patch(_PATCH_GEN, return_value=[_FAKE_URL])
    def test_fallback_promo_text_when_claude_fails(self, mock_gen, mock_claude):
        result = generate_party_promo("halloween")
        assert len(result["promo_text"]) > 5

    @patch(_PATCH_CLAUDE, return_value="promo")
    @patch(_PATCH_GEN, return_value=[])
    def test_empty_url_when_no_generation(self, mock_gen, mock_claude):
        result = generate_party_promo("summer")
        assert result["poster_url"] == ""

    @patch(_PATCH_CLAUDE, return_value="promo")
    @patch(_PATCH_GEN, return_value=[_FAKE_URL])
    def test_uses_vertical_aspect_ratio(self, mock_gen, mock_claude):
        generate_party_promo("corporate")
        assert mock_gen.call_args[1].get("aspect_ratio") == "9:16"

    @patch(_PATCH_CLAUDE, return_value="promo")
    @patch(_PATCH_GEN, return_value=[_FAKE_URL])
    def test_theme_in_result(self, mock_gen, mock_claude):
        result = generate_party_promo("masquerade")
        assert result["theme"] == "masquerade"

    @patch(_PATCH_CLAUDE, return_value="promo")
    @patch(_PATCH_GEN, return_value=[_FAKE_URL])
    def test_custom_text_passed_to_prompt(self, mock_gen, mock_claude):
        generate_party_promo("pool", custom_text="VIP только")
        prompt = mock_gen.call_args[0][0]
        assert "VIP только" in prompt


class TestGenerateInviteCard:
    @patch(_PATCH_GEN, return_value=[_FAKE_URL])
    def test_returns_required_keys(self, mock_gen):
        result = generate_invite_card("Иван", "День Рождения", "5 мая")
        assert "card_url" in result
        assert "personal_text" in result
        assert "guest_name" in result
        assert "event" in result
        assert "date" in result

    @patch(_PATCH_GEN, return_value=[_FAKE_URL])
    def test_card_url_is_result(self, mock_gen):
        result = generate_invite_card("Мария", "корпоратив", "10 июня")
        assert result["card_url"] == _FAKE_URL

    @patch(_PATCH_GEN, return_value=[_FAKE_URL])
    def test_personal_text_contains_guest_name(self, mock_gen):
        result = generate_invite_card("Алексей", "Новый год", "31 декабря")
        assert "Алексей" in result["personal_text"]

    @patch(_PATCH_GEN, return_value=[_FAKE_URL])
    def test_personal_text_contains_date(self, mock_gen):
        result = generate_invite_card("Анна", "Wedding", "15 июля")
        assert "15 июля" in result["personal_text"]

    @patch(_PATCH_GEN, return_value=[_FAKE_URL])
    def test_uses_card_aspect_ratio(self, mock_gen):
        generate_invite_card("Пётр", "birthday", "1 апреля")
        assert mock_gen.call_args[1].get("aspect_ratio") == "3:4"

    @patch(_PATCH_GEN, return_value=[])
    def test_empty_url_when_no_generation(self, mock_gen):
        result = generate_invite_card("X", "event", "date")
        assert result["card_url"] == ""


class TestGenerateEventPhoto:
    @patch(_PATCH_GEN, return_value=[_FAKE_URL])
    def test_returns_url(self, mock_gen):
        url = generate_event_photo("banquet hall with crystal chandeliers")
        assert url == _FAKE_URL

    @patch(_PATCH_GEN, return_value=[])
    def test_returns_empty_on_no_output(self, mock_gen):
        assert generate_event_photo("empty venue") == ""

    @patch(_PATCH_GEN, return_value=[_FAKE_URL])
    def test_uses_landscape_aspect(self, mock_gen):
        generate_event_photo("rooftop venue")
        assert mock_gen.call_args[1].get("aspect_ratio") == "16:9"
