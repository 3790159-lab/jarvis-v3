"""Tests for Block H4: Photo Studio Telegram integration (H4.1–H4.6)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# Ensure project root is on path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from tools.photo_studio_telegram import (
    _kb,
    _social_post_keyboard,
    _confirm_faceswap_keyboard,
    _lora_confirm_keyboard,
    _photo_router_keyboard,
    clear_conv,
    handle_dish_styles,
    handle_enhance_start,
    handle_event_photo,
    handle_faceswap_callback,
    handle_faceswap_photo_step,
    handle_faceswap_start,
    handle_invite_card,
    handle_lora_callback,
    handle_lora_delete,
    handle_lora_done,
    handle_lora_list,
    handle_lora_name_text,
    handle_lora_status,
    handle_lora_train_start,
    handle_lora_trigger_text,
    handle_me_as,
    handle_me_in,
    handle_me_into_start,
    handle_me_places,
    handle_me_roles,
    handle_me_style,
    handle_me_styles,
    handle_me_with,
    handle_menu_book,
    handle_menu_photo,
    handle_party_promo,
    handle_party_themes,
    handle_photo_router_callback,
    handle_photo_router_text,
    handle_social_post,
    load_conv,
    save_conv,
)


# ── fixtures ─────────────────────────────────────────────────────────────────

CHAT = "test_chat_42"


@pytest.fixture(autouse=True)
def _allow_money_gate(monkeypatch):
    """Photo Studio команды теперь за money-гейтом (Арка 1). Тесты ниже проверяют
    ПОВЕДЕНИЕ генерации/доставки, поэтому гейт пропускаем (allow, pass-through).
    Сам гейт (отказ при over-limit, запись в леджер) проверяется отдельно в
    tests/test_money_gate_photo_studio.py."""
    import tools.photo_studio_telegram as _ps
    if hasattr(_ps, "guard_spend"):
        monkeypatch.setattr(_ps, "guard_spend", lambda uid, un, est, do: (do(), None))


@pytest.fixture(autouse=True)
def clean_conv():
    """Clear conversation state before/after each test."""
    clear_conv(CHAT)
    yield
    clear_conv(CHAT)


def make_send():
    return MagicMock(name="send")


def make_photo():
    return MagicMock(name="send_photo")


# ── Keyboard builders ─────────────────────────────────────────────────────────

class TestKeyboardBuilders:
    def test_kb_returns_inline_keyboard(self):
        result = _kb([[{"text": "OK", "callback_data": "x"}]])
        assert "inline_keyboard" in result
        assert len(result["inline_keyboard"]) == 1

    def test_social_post_keyboard_has_three_buttons(self):
        kb = _social_post_keyboard("борщ")
        buttons = kb["inline_keyboard"][0]
        assert len(buttons) == 3
        texts = [b["text"] for b in buttons]
        assert any("Obsidian" in t for t in texts)
        assert any("Instagram" in t for t in texts)
        assert any("Перегенерировать" in t for t in texts)

    def test_social_post_keyboard_callback_contains_dish(self):
        kb = _social_post_keyboard("тирамису")
        for btn in kb["inline_keyboard"][0]:
            assert "тирамису" in btn["callback_data"]

    def test_confirm_faceswap_has_basic_and_polish(self):
        kb = _confirm_faceswap_keyboard()
        all_buttons = [b for row in kb["inline_keyboard"] for b in row]
        cb_data = [b["callback_data"] for b in all_buttons]
        assert "fs:exec:basic" in cb_data
        assert "fs:exec:polish" in cb_data
        assert "fs:cancel" in cb_data

    def test_lora_confirm_keyboard_has_confirm_cancel(self):
        kb = _lora_confirm_keyboard()
        all_buttons = [b for row in kb["inline_keyboard"] for b in row]
        cb_data = [b["callback_data"] for b in all_buttons]
        assert "lora:confirm" in cb_data
        assert "lora:cancel" in cb_data

    def test_photo_router_keyboard_has_three_actions(self):
        kb = _photo_router_keyboard("restaurant")
        all_buttons = [b for row in kb["inline_keyboard"] for b in row]
        cb_data = [b["callback_data"] for b in all_buttons]
        assert any("pr:confirm:restaurant" == d for d in cb_data)
        assert "pr:change" in cb_data
        assert "pr:cancel" in cb_data


# ── Conversation state ────────────────────────────────────────────────────────

class TestConversationState:
    def test_save_and_load(self):
        save_conv(CHAT, {"step": "test", "data": {"x": 1}})
        loaded = load_conv(CHAT)
        assert loaded["step"] == "test"
        assert loaded["data"]["x"] == 1

    def test_load_missing_returns_empty(self):
        clear_conv(CHAT)
        result = load_conv(CHAT)
        assert result == {}

    def test_clear_removes_state(self):
        save_conv(CHAT, {"step": "test"})
        clear_conv(CHAT)
        assert load_conv(CHAT) == {}


# ── H4.1 Restaurant ────────────────────────────────────────────────────────────

class TestMenuPhoto:
    def test_no_query_sends_usage(self):
        s = make_send()
        handle_menu_photo(CHAT, "", s, make_photo())
        msg = s.call_args[0][1]
        assert "Использование" in msg or "/menu_photo" in msg

    def test_calls_generate_dish_photo(self):
        s = make_send()
        p = make_photo()
        with patch("app.services.restaurant_mode.generate_dish_photo",
                   return_value="http://img.test/dish.jpg") as mock_gen:
            handle_menu_photo(CHAT, "борщ", s, p)
            mock_gen.assert_called_once()
            args = mock_gen.call_args[0]
            assert "борщ" in args[0]

    def test_sends_photo_on_success(self):
        s = make_send()
        p = make_photo()
        with patch("app.services.restaurant_mode.generate_dish_photo",
                   return_value="http://img.test/dish.jpg"):
            handle_menu_photo(CHAT, "борщ", s, p)
            p.assert_called_once_with(CHAT, "http://img.test/dish.jpg", caption="борщ")

    def test_parses_style_flag(self):
        s = make_send()
        p = make_photo()
        with patch("app.services.restaurant_mode.generate_dish_photo",
                   return_value="http://x.test/x.jpg") as mock_gen:
            handle_menu_photo(CHAT, "борщ --style dark", s, p)
            args = mock_gen.call_args[0]
            assert args[1] == "dark"

    def test_error_is_reported(self):
        s = make_send()
        with patch("app.services.restaurant_mode.generate_dish_photo",
                   side_effect=RuntimeError("boom")):
            handle_menu_photo(CHAT, "блюдо", s, make_photo())
            msg = s.call_args[0][1]
            assert "❌" in msg

    def test_menu_photo_handler_success(self):
        """generate_dish_photo returns str URL — handler must send photo."""
        s = make_send()
        p = make_photo()
        with patch("app.services.restaurant_mode.generate_dish_photo",
                   return_value="https://cdn.test/borscht.jpg"):
            handle_menu_photo(CHAT, "борщ", s, p)
        p.assert_called_once_with(CHAT, "https://cdn.test/borscht.jpg", caption="борщ")

    def test_menu_photo_empty_result(self):
        """generate_dish_photo returns empty str — handler sends error, no photo."""
        s = make_send()
        p = make_photo()
        with patch("app.services.restaurant_mode.generate_dish_photo", return_value=""):
            handle_menu_photo(CHAT, "борщ", s, p)
        p.assert_not_called()
        assert "❌" in s.call_args[0][1]


class TestSocialPost:
    def test_no_query_sends_usage(self):
        s = make_send()
        handle_social_post(CHAT, "", s, make_photo())
        msg = s.call_args[0][1]
        assert "/social_post" in msg

    def test_calls_generate_social_post(self):
        s = make_send()
        with patch("app.services.restaurant_mode.generate_social_post",
                   return_value={"url": "http://x.test/x.jpg", "caption": "Cap", "hashtags": "#tag"}), \
             patch("tools.jarvis_smart_telegram_control.tg_call"):
            handle_social_post(CHAT, "борщ", s, make_photo())

    def test_error_is_caught(self):
        s = make_send()
        with patch("app.services.restaurant_mode.generate_social_post",
                   side_effect=Exception("fail")):
            handle_social_post(CHAT, "блюдо", s, make_photo())
            assert "❌" in s.call_args[0][1]


class TestMenuBook:
    def test_no_query_sends_usage(self):
        s = make_send()
        handle_menu_book(CHAT, "", s, make_photo())
        assert s.called

    def test_generates_series(self):
        s = make_send()
        p = make_photo()
        with patch("app.services.restaurant_mode.generate_menu_series",
                   return_value=[{"url": "http://a.test/1.jpg", "dish": "борщ"},
                                  {"url": "http://a.test/2.jpg", "dish": "стейк"}]):
            handle_menu_book(CHAT, "борщ, стейк", s, p)
            assert p.call_count == 2

    def test_reports_count(self):
        s = make_send()
        with patch("app.services.restaurant_mode.generate_menu_series",
                   return_value=[{"url": "http://a.test/1.jpg", "dish": "борщ"}]):
            handle_menu_book(CHAT, "борщ", s, make_photo())
            final_msg = s.call_args[0][1]
            assert "1/1" in final_msg


class TestDishStyles:
    def test_shows_four_styles(self):
        s = make_send()
        with patch("app.services.restaurant_mode.FOOD_TEMPLATES",
                   {"rustic": "tmpl", "modern": "tmpl", "dark": "tmpl", "instagram": "tmpl"}), \
             patch("app.services.restaurant_mode.list_styles",
                   return_value=["rustic", "modern", "dark", "instagram"]):
            handle_dish_styles(CHAT, s)
            msg = s.call_args[0][1]
            assert "rustic" in msg


# ── H4.2 Party ────────────────────────────────────────────────────────────────

class TestPartyPromo:
    def test_no_query_sends_usage(self):
        s = make_send()
        handle_party_promo(CHAT, "", s, make_photo())
        assert s.called

    def test_calls_generate_party_promo(self):
        s = make_send()
        p = make_photo()
        with patch("app.services.party_mode.generate_party_promo",
                   return_value={"poster_url": "http://party.test/p.jpg", "promo_text": "Party!",
                                 "theme": "halloween", "format": "9:16"}):
            handle_party_promo(CHAT, "halloween", s, p)
            p.assert_called_once()

    def test_error_reported(self):
        s = make_send()
        with patch("app.services.party_mode.generate_party_promo",
                   side_effect=Exception("fail")):
            handle_party_promo(CHAT, "nye", s, make_photo())
            assert "❌" in s.call_args[0][1]

    def test_party_promo_success(self):
        """Handler reads poster_url from result dict."""
        s = make_send()
        p = make_photo()
        with patch("app.services.party_mode.generate_party_promo",
                   return_value={"poster_url": "https://cdn.test/poster.jpg",
                                 "promo_text": "Halloween Night!", "theme": "halloween",
                                 "format": "9:16"}):
            handle_party_promo(CHAT, "halloween", s, p)
        p.assert_called_once_with(CHAT, "https://cdn.test/poster.jpg", caption="Halloween Night!")

    def test_party_promo_no_poster(self):
        """Empty poster_url sends error message, no photo."""
        s = make_send()
        p = make_photo()
        with patch("app.services.party_mode.generate_party_promo",
                   return_value={"poster_url": "", "promo_text": "text", "theme": "nye",
                                 "format": "9:16"}):
            handle_party_promo(CHAT, "nye", s, p)
        p.assert_not_called()
        assert "❌" in s.call_args[0][1]

    def test_party_promo_long_text_split(self):
        """Promo text > 1024 chars: photo + separate full text message."""
        s = make_send()
        p = make_photo()
        long_text = "X" * 2000
        with patch("app.services.party_mode.generate_party_promo",
                   return_value={"poster_url": "https://cdn.test/p.jpg",
                                 "promo_text": long_text, "theme": "nye", "format": "9:16"}):
            handle_party_promo(CHAT, "nye", s, p)
        p.assert_called_once()
        assert s.call_count >= 2
        full_text_call = s.call_args_list[-1][0][1]
        assert "Полный текст" in full_text_call


class TestInviteCard:
    def test_no_query_sends_usage(self):
        s = make_send()
        handle_invite_card(CHAT, "", s, make_photo())
        assert s.called

    def test_three_tokens_required(self):
        s = make_send()
        handle_invite_card(CHAT, "Иван", s, make_photo())
        assert "3 параметра" in s.call_args[0][1] or s.called

    def test_calls_generate_invite_card(self):
        s = make_send()
        p = make_photo()
        with patch("app.services.party_mode.generate_invite_card",
                   return_value={"card_url": "http://card.test/c.jpg", "personal_text": "Дорогой Иван!"}):
            handle_invite_card(CHAT, 'Иван "ДР" "5 мая"', s, p)
            p.assert_called_once()


class TestEventPhoto:
    def test_no_query_sends_usage(self):
        s = make_send()
        handle_event_photo(CHAT, "", s, make_photo())
        assert s.called

    def test_calls_generate_event_photo(self):
        s = make_send()
        p = make_photo()
        with patch("app.services.party_mode.generate_event_photo",
                   return_value={"url": "http://ev.test/e.jpg"}):
            handle_event_photo(CHAT, "банкетный зал", s, p)
            p.assert_called_once()


class TestPartyThemes:
    def test_shows_themes(self):
        s = make_send()
        with patch("app.services.party_mode.PARTY_THEMES",
                   {"halloween": {}, "nye": {}}):
            handle_party_themes(CHAT, s)
            msg = s.call_args[0][1]
            assert "halloween" in msg


# ── H4.3 Face Swap ────────────────────────────────────────────────────────────

class TestFaceSwapStart:
    def test_sets_conversation_step(self):
        s = make_send()
        handle_faceswap_start(CHAT, s)
        conv = load_conv(CHAT)
        assert conv["step"] == "faceswap_source"

    def test_sends_instructions(self):
        s = make_send()
        handle_faceswap_start(CHAT, s)
        msg = s.call_args[0][1]
        assert "source" in msg.lower() or "Шаг 1" in msg


class TestEnhanceStart:
    def test_sets_enhance_step(self):
        s = make_send()
        handle_enhance_start(CHAT, s)
        assert load_conv(CHAT)["step"] == "enhance_upload"


class TestMeIntoStart:
    def test_sets_meinto_step(self):
        s = make_send()
        handle_me_into_start(CHAT, s)
        assert load_conv(CHAT)["step"] == "meinto_target"


class TestFaceSwapPhotoStep:
    # DEV-74: шаг хранит file_id, а не готовую ссылку — см.
    # tests/test_photo_studio_no_token_at_rest.py
    def test_source_step_advances(self):
        save_conv(CHAT, {"step": "faceswap_source", "data": {}})
        s = make_send()
        consumed = handle_faceswap_photo_step(
            CHAT, "http://img.test/face.jpg", s, make_photo(), file_id="FID_SRC")
        assert consumed
        assert load_conv(CHAT)["step"] == "faceswap_target"
        assert load_conv(CHAT)["data"]["source_file_id"] == "FID_SRC"

    def test_target_step_advances_to_confirm(self):
        save_conv(CHAT, {"step": "faceswap_target", "data": {"source_file_id": "FID_SRC"}})
        s = make_send()
        with patch("tools.jarvis_smart_telegram_control.tg_call"):
            consumed = handle_faceswap_photo_step(
                CHAT, "http://img.test/target.jpg", s, make_photo(), file_id="FID_TGT")
        assert consumed
        assert load_conv(CHAT)["step"] == "faceswap_confirm"
        assert load_conv(CHAT)["data"]["target_file_id"] == "FID_TGT"

    def test_no_active_conv_returns_false(self):
        s = make_send()
        result = handle_faceswap_photo_step(CHAT, "http://x.test/x.jpg", s, make_photo())
        assert not result

    def test_enhance_step_calls_gfpgan(self):
        save_conv(CHAT, {"step": "enhance_upload", "data": {}})
        s = make_send()
        p = make_photo()
        with patch("app.services.face_swap.enhance_face", return_value="http://enhanced.test/r.jpg"):
            consumed = handle_faceswap_photo_step(CHAT, "http://ph.test/photo.jpg", s, p)
        assert consumed
        p.assert_called_once()

    def test_lora_collecting_adds_photo(self):
        save_conv(CHAT, {"step": "lora_collecting", "data": {"photo_file_ids": []}})
        s = make_send()
        handle_faceswap_photo_step(
            CHAT, "http://face.test/1.jpg", s, make_photo(), file_id="FID_1")
        assert load_conv(CHAT)["data"]["photo_file_ids"] == ["FID_1"]


class TestFaceSwapCallback:
    def test_cancel_clears_conv(self):
        save_conv(CHAT, {"step": "faceswap_confirm", "data": {}})
        s = make_send()
        handled = handle_faceswap_callback(CHAT, "fs:cancel", s, make_photo())
        assert handled
        assert load_conv(CHAT) == {}

    # DEV-74: в состоянии лежат file_id, ссылка разменивается в момент вызова.
    # `_resolve_photo_url` подменён, иначе тест пошёл бы в сеть за getFile.
    def test_exec_basic_calls_face_swap(self):
        save_conv(CHAT, {"step": "faceswap_confirm",
                         "data": {"source_file_id": "S", "target_file_id": "T"}})
        s = make_send()
        p = make_photo()
        with patch("tools.photo_studio_telegram._resolve_photo_url",
                   side_effect=lambda fid: f"http://res.test/{fid}.jpg"), \
             patch("app.services.face_swap.face_swap_basic", return_value="http://res.test/r.jpg"):
            handled = handle_faceswap_callback(CHAT, "fs:exec:basic", s, p)
        assert handled
        p.assert_called()

    def test_exec_polish_calls_polished_swap(self):
        save_conv(CHAT, {"step": "faceswap_confirm",
                         "data": {"source_file_id": "S", "target_file_id": "T"}})
        s = make_send()
        p = make_photo()
        with patch("tools.photo_studio_telegram._resolve_photo_url",
                   side_effect=lambda fid: f"http://res.test/{fid}.jpg"), \
             patch("app.services.face_swap.face_swap_with_polish", return_value="http://pol.test/r.jpg") as swap:
            handled = handle_faceswap_callback(CHAT, "fs:exec:polish", s, p)
        assert handled
        # 🔴 Без этой строки тест зеленел ВХОЛОСТУЮ: при пустом состоянии флоу
        # уходит в «не найдены фото» и тоже возвращает True.
        swap.assert_called_once()

    def test_non_fs_callback_returns_false(self):
        s = make_send()
        assert not handle_faceswap_callback(CHAT, "other:data", s, make_photo())


# ── H4.4 LoRA ─────────────────────────────────────────────────────────────────

class TestLoraTrain:
    def test_start_sets_collecting_step(self):
        s = make_send()
        handle_lora_train_start(CHAT, s)
        assert load_conv(CHAT)["step"] == "lora_collecting"
        assert load_conv(CHAT)["data"]["photos"] == []

    def test_done_too_few_photos(self):
        save_conv(CHAT, {"step": "lora_collecting", "data": {"photos": ["x"] * 5}})
        s = make_send()
        handle_lora_done(CHAT, s)
        assert "только" in s.call_args[0][1] or "5 фото" in s.call_args[0][1]

    def test_done_enough_photos_advances(self):
        save_conv(CHAT, {"step": "lora_collecting", "data": {"photos": ["x"] * 12}})
        s = make_send()
        handle_lora_done(CHAT, s)
        assert load_conv(CHAT)["step"] == "lora_name"

    def test_done_without_collecting_sends_error(self):
        s = make_send()
        handle_lora_done(CHAT, s)
        assert s.called


class TestLoraTriggerName:
    def test_name_text_advances_to_trigger(self):
        save_conv(CHAT, {"step": "lora_name", "data": {"photos": ["x"] * 12}})
        s = make_send()
        consumed = handle_lora_name_text(CHAT, "Daniil", s)
        assert consumed
        assert load_conv(CHAT)["step"] == "lora_trigger"

    def test_name_text_saves_name(self):
        save_conv(CHAT, {"step": "lora_name", "data": {}})
        s = make_send()
        handle_lora_name_text(CHAT, "Daniil", s)
        assert load_conv(CHAT)["data"]["name"] == "Daniil"

    def test_not_in_name_step_returns_false(self):
        s = make_send()
        assert not handle_lora_name_text(CHAT, "some text", s)

    def test_trigger_text_advances_to_confirm(self):
        save_conv(CHAT, {"step": "lora_trigger", "data": {"name": "Daniil"}})
        s = make_send()
        with patch("tools.jarvis_smart_telegram_control.tg_call"):
            consumed = handle_lora_trigger_text(CHAT, "DANIIL", s)
        assert consumed
        assert load_conv(CHAT)["step"] == "lora_confirm"

    def test_trigger_skip_uses_default(self):
        save_conv(CHAT, {"step": "lora_trigger", "data": {"name": "Daniil"}})
        s = make_send()
        with patch("tools.jarvis_smart_telegram_control.tg_call"):
            handle_lora_trigger_text(CHAT, "/skip", s)
        assert load_conv(CHAT)["data"]["trigger_word"] == "DANIIL"


class TestLoraCallback:
    def test_cancel_clears_conv(self):
        save_conv(CHAT, {"step": "lora_confirm", "data": {}})
        s = make_send()
        handled = handle_lora_callback(CHAT, "lora:cancel", s)
        assert handled
        assert load_conv(CHAT) == {}

    def test_confirm_calls_start_training(self):
        save_conv(CHAT, {
            "step": "lora_confirm",
            "data": {"photos": ["u1", "u2"] * 8, "name": "Daniil", "trigger_word": "DANIIL"},
        })
        s = make_send()
        with patch("app.services.lora_manager.start_lora_training",
                   return_value={"training_id": "tid123"}), \
             patch("tools.photo_studio_telegram._start_lora_poll"):
            handled = handle_lora_callback(CHAT, "lora:confirm", s)
        assert handled
        assert "tid123" in s.call_args[0][1] or s.called

    def test_non_lora_returns_false(self):
        s = make_send()
        assert not handle_lora_callback(CHAT, "other:data", s)


class TestLoraListStatus:
    def test_list_no_loras(self):
        s = make_send()
        with patch("app.services.lora_manager.list_loras", return_value=[]):
            handle_lora_list(CHAT, s)
            assert "нет" in s.call_args[0][1].lower() or "/lora_train" in s.call_args[0][1]

    def test_list_shows_models(self):
        s = make_send()
        with patch("app.services.lora_manager.list_loras",
                   return_value=[{"name": "Daniil", "trigger_word": "DANIIL", "status": "succeeded"}]):
            handle_lora_list(CHAT, s)
            assert "Daniil" in s.call_args[0][1]

    def test_status_no_query(self):
        s = make_send()
        handle_lora_status(CHAT, "", s)
        assert s.called

    def test_status_not_found(self):
        s = make_send()
        with patch("app.services.lora_manager.check_lora_status", return_value=None):
            handle_lora_status(CHAT, "NoModel", s)
            assert "не найдена" in s.call_args[0][1]

    def test_delete_no_query(self):
        s = make_send()
        handle_lora_delete(CHAT, "", s)
        assert s.called

    def test_delete_success(self):
        s = make_send()
        with patch("app.services.lora_manager.delete_lora", return_value=True):
            handle_lora_delete(CHAT, "Daniil", s)
            assert "✅" in s.call_args[0][1]

    def test_delete_not_found(self):
        s = make_send()
        with patch("app.services.lora_manager.delete_lora", return_value=False):
            handle_lora_delete(CHAT, "NoModel", s)
            assert "❌" in s.call_args[0][1]


# ── H4.5 Personal Mode ─────────────────────────────────────────────────────────

class TestPersonalCommands:
    def _with_lora(self):
        return patch("tools.photo_studio_telegram._check_lora_available", return_value=True)

    def _no_lora(self):
        return patch("tools.photo_studio_telegram._check_lora_available", return_value=False)

    def test_me_as_no_lora_blocked(self):
        s = make_send()
        with self._no_lora():
            handle_me_as(CHAT, "bodybuilder", s, make_photo())
            assert "/lora_train" in s.call_args[0][1]

    def test_me_as_no_query(self):
        s = make_send()
        handle_me_as(CHAT, "", s, make_photo())
        assert s.called

    def test_me_as_generates(self):
        s = make_send()
        p = make_photo()
        with self._with_lora(), \
             patch("app.services.personal_mode.generate_me_as",
                   return_value={"url": "http://me.test/as.jpg"}):
            handle_me_as(CHAT, "bodybuilder", s, p)
            p.assert_called_once()

    def test_me_in_no_lora_blocked(self):
        s = make_send()
        with self._no_lora():
            handle_me_in(CHAT, "maldives", s, make_photo())
            assert "/lora_train" in s.call_args[0][1]

    def test_me_in_generates(self):
        s = make_send()
        p = make_photo()
        with self._with_lora(), \
             patch("app.services.personal_mode.generate_me_in",
                   return_value={"url": "http://me.test/in.jpg"}):
            handle_me_in(CHAT, "maldives", s, p)
            p.assert_called_once()

    def test_me_with_generates(self):
        s = make_send()
        p = make_photo()
        with self._with_lora(), \
             patch("app.services.personal_mode.generate_me_in",
                   return_value={"url": "http://me.test/with.jpg"}):
            handle_me_with(CHAT, "Lambo", s, p)
            p.assert_called_once()

    def test_me_style_generates(self):
        s = make_send()
        p = make_photo()
        with self._with_lora(), \
             patch("app.services.personal_mode.generate_me_in_style",
                   return_value={"url": "http://me.test/style.jpg"}):
            handle_me_style(CHAT, "cyberpunk", s, p)
            p.assert_called_once()

    def test_me_roles_lists_roles(self):
        s = make_send()
        with patch("app.services.personal_mode.ROLES_TEMPLATES",
                   {"bodybuilder": "tmpl", "chef": "tmpl"}):
            handle_me_roles(CHAT, s)
            assert "bodybuilder" in s.call_args[0][1]

    def test_me_places_lists_places(self):
        s = make_send()
        with patch("app.services.personal_mode.PLACES_TEMPLATES",
                   {"maldives": "tmpl", "paris": "tmpl"}):
            handle_me_places(CHAT, s)
            assert "maldives" in s.call_args[0][1]

    def test_me_styles_lists_styles(self):
        s = make_send()
        with patch("app.services.personal_mode.STYLE_TEMPLATES",
                   {"cyberpunk": "tmpl", "anime": "tmpl"}):
            handle_me_styles(CHAT, s)
            assert "cyberpunk" in s.call_args[0][1]


# ── H4.6 Smart Photo Router ────────────────────────────────────────────────────

class TestPhotoRouter:
    def test_food_text_detected(self):
        s = make_send()
        with patch("app.services.smart_photo_router.analyze_photo_request",
                   return_value={"is_photo_request": True, "pipeline": "restaurant",
                                 "confidence": 0.9, "reasoning": "food keywords"}), \
             patch("app.services.smart_photo_router.format_pipeline_suggestion",
                   return_value="Рекомендую: Restaurant"), \
             patch("tools.jarvis_smart_telegram_control.tg_call"):
            handled = handle_photo_router_text(CHAT, "сделай фото борща", s, make_photo())
        assert handled
        conv = load_conv(CHAT)
        assert conv["step"] == "photo_router_confirm"
        assert conv["data"]["pipeline"] == "restaurant"

    def test_non_photo_text_not_handled(self):
        s = make_send()
        with patch("app.services.smart_photo_router.analyze_photo_request",
                   return_value={"is_photo_request": False}):
            handled = handle_photo_router_text(CHAT, "расскажи о ресторане", s, make_photo())
        assert not handled

    def test_cancel_callback(self):
        save_conv(CHAT, {"step": "photo_router_confirm", "data": {"query": "x"}})
        s = make_send()
        handled = handle_photo_router_callback(CHAT, "pr:cancel", s, make_photo())
        assert handled
        assert load_conv(CHAT) == {}

    def test_change_callback(self):
        save_conv(CHAT, {"step": "photo_router_confirm", "data": {}})
        s = make_send()
        handled = handle_photo_router_callback(CHAT, "pr:change", s, make_photo())
        assert handled

    def test_confirm_executes_pipeline(self):
        save_conv(CHAT, {"step": "photo_router_confirm", "data": {"query": "борщ"}})
        s = make_send()
        p = make_photo()
        with patch("app.services.restaurant_mode.generate_dish_photo",
                   return_value={"url": "http://food.test/b.jpg"}):
            handled = handle_photo_router_callback(CHAT, "pr:confirm:restaurant", s, p)
        assert handled

    def test_unknown_callback_returns_false(self):
        s = make_send()
        assert not handle_photo_router_callback(CHAT, "other:x", s, make_photo())


# ── get_telegram_photo_url ────────────────────────────────────────────────────

class TestGetTelegramPhotoUrl:
    def test_returns_url_on_success(self):
        from tools.photo_studio_telegram import get_telegram_photo_url
        import urllib.request as _ur
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "result": {"file_path": "photos/file_123.jpg"}
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            url = get_telegram_photo_url("fid123", "BOT_TOKEN")
        assert url == "https://api.telegram.org/file/botBOT_TOKEN/photos/file_123.jpg"

    def test_returns_none_on_error(self):
        from tools.photo_studio_telegram import get_telegram_photo_url
        with patch("urllib.request.urlopen", side_effect=Exception("fail")):
            url = get_telegram_photo_url("fid", "TOKEN")
        assert url is None
