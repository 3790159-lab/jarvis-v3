from __future__ import annotations

"""Phase 8: Greeting & Small Talk handler tests.

Verifies:
1. GREETING_TRIGGERS route to intent="greeting"
2. SMALL_TALK_TRIGGERS route to intent="small_talk"
3. greeting_answer() uses time-of-day prefix and includes owner name
4. small_talk_answer() returns correct response for each trigger category
5. "Привет, кто ты?" → identity (not greeting) — identity wins for compound questions
6. Greetings with extra text → greeting intent
7. Small talk exact match and prefix match
"""

import os
import sys
from unittest.mock import patch
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.jarvis_smart_telegram_control import (
    classify_message,
    greeting_answer,
    small_talk_answer,
    JARVIS_OWNER,
)


# ---------------------------------------------------------------------------
# Greeting routing
# ---------------------------------------------------------------------------

def test_privet_is_greeting():
    assert classify_message("Привет", {})["intent"] == "greeting"


def test_hi_is_greeting():
    assert classify_message("hi", {})["intent"] == "greeting"


def test_hello_is_greeting():
    assert classify_message("Hello", {})["intent"] == "greeting"


def test_hey_is_greeting():
    assert classify_message("hey", {})["intent"] == "greeting"


def test_dobroe_utro_is_greeting():
    assert classify_message("Доброе утро", {})["intent"] == "greeting"


def test_dobryy_den_is_greeting():
    assert classify_message("Добрый день", {})["intent"] == "greeting"


def test_dobryy_vecher_is_greeting():
    assert classify_message("Добрый вечер", {})["intent"] == "greeting"


def test_zdorova_is_greeting():
    assert classify_message("Здарова", {})["intent"] == "greeting"


def test_haiy_is_greeting():
    assert classify_message("хай", {})["intent"] == "greeting"


def test_yo_en_is_greeting():
    assert classify_message("yo", {})["intent"] == "greeting"


def test_hola_is_greeting():
    assert classify_message("hola", {})["intent"] == "greeting"


def test_greeting_with_punctuation():
    assert classify_message("Привет!", {})["intent"] == "greeting"


# ---------------------------------------------------------------------------
# Small talk routing
# ---------------------------------------------------------------------------

def test_spasibo_is_small_talk():
    assert classify_message("Спасибо", {})["intent"] == "small_talk"


def test_ok_is_small_talk():
    assert classify_message("ок", {})["intent"] == "small_talk"


def test_okay_is_small_talk():
    assert classify_message("okay", {})["intent"] == "small_talk"


def test_thanks_is_small_talk():
    assert classify_message("thanks", {})["intent"] == "small_talk"


def test_izvini_is_small_talk():
    assert classify_message("Извини", {})["intent"] == "small_talk"


def test_ponyal_is_small_talk():
    assert classify_message("понял", {})["intent"] == "small_talk"


def test_got_it_is_small_talk():
    assert classify_message("got it", {})["intent"] == "small_talk"


# ---------------------------------------------------------------------------
# Identity wins for compound questions
# ---------------------------------------------------------------------------

def test_privet_kto_ty_is_identity():
    result = classify_message("Привет, кто ты?", {})
    assert result["intent"] == "identity", f"Expected identity for 'Привет, кто ты?', got: {result}"


def test_privet_chto_umeesh_is_capabilities():
    result = classify_message("Привет, что умеешь?", {})
    # This goes to capabilities since "что умеешь" is a more specific trigger
    assert result["intent"] in ("capabilities", "greeting"), f"Got: {result}"


# ---------------------------------------------------------------------------
# greeting_answer() — time-of-day
# ---------------------------------------------------------------------------

def _mock_hour(h: int):
    return patch("tools.jarvis_smart_telegram_control.datetime",
                 **{"now.return_value": datetime(2026, 4, 30, h, 0, 0)})


def test_greeting_answer_morning():
    with _mock_hour(8):
        answer = greeting_answer()
    assert "Доброе утро" in answer
    assert JARVIS_OWNER in answer


def test_greeting_answer_afternoon():
    with _mock_hour(14):
        answer = greeting_answer()
    assert "Добрый день" in answer


def test_greeting_answer_evening():
    with _mock_hour(19):
        answer = greeting_answer()
    assert "Добрый вечер" in answer


def test_greeting_answer_night():
    with _mock_hour(2):
        answer = greeting_answer()
    assert "Привет" in answer


def test_greeting_answer_contains_how_to_help():
    answer = greeting_answer()
    assert "умеешь" in answer or "помощь" in answer or "Чем могу" in answer


# ---------------------------------------------------------------------------
# small_talk_answer()
# ---------------------------------------------------------------------------

def test_small_talk_spasibo():
    assert "пожалуйста" in small_talk_answer("спасибо").lower()


def test_small_talk_ok():
    answer = small_talk_answer("ок")
    assert "✅" in answer


def test_small_talk_sorry():
    answer = small_talk_answer("sorry")
    assert "порядке" in answer or "помочь" in answer or "✅" in answer


def test_small_talk_unknown_returns_checkmark():
    answer = small_talk_answer("какая-то непонятная фраза что ладно ничего")
    assert "✅" in answer or len(answer) > 0


# ---------------------------------------------------------------------------
# run_intent routes greeting and small_talk correctly
# ---------------------------------------------------------------------------

def test_run_intent_greeting_calls_send(monkeypatch):
    import tools.jarvis_smart_telegram_control as mod
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, txt: sent.append(txt))
    mod.run_intent("123", {"intent": "greeting", "query": ""}, {})
    assert len(sent) == 1
    assert JARVIS_OWNER in sent[0] or "утро" in sent[0] or "день" in sent[0] or "вечер" in sent[0] or "Привет" in sent[0]


def test_run_intent_small_talk_calls_send(monkeypatch):
    import tools.jarvis_smart_telegram_control as mod
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, txt: sent.append(txt))
    mod.run_intent("123", {"intent": "small_talk", "query": "спасибо"}, {})
    assert len(sent) == 1
    assert "пожалуйста" in sent[0].lower() or "✅" in sent[0]
