from __future__ import annotations
import pytest
from chatter.core.disclosure import is_bot_question, honest_disclosure, HONESTY_MARKER

BOT_QUESTIONS = [
    "ты бот?",
    "Ты бот или человек?",
    "это бот?",
    "я с ботом разговариваю?",
    "ты живой человек?",
    "с кем я говорю, с ботом?",
    "ты автоответчик?",
    "ты реальный человек или ии?",
    "are you a bot?",
    "is this a bot or a real person?",
]

# Phrasings with words BETWEEN "ты" and "бот" that a live demo showed slip
# past a too-strict adjacency-only regex.
BOT_QUESTIONS_WITH_GAP = [
    "а ты вообще бот?",
    "ты что, бот?",
    "ты не бот?",
    "ты случайно не бот?",
    "а ты бот или человек?",
    "ты робот?",
    "это автоответчик?",
]

@pytest.mark.parametrize("q", BOT_QUESTIONS)
def test_detects_bot_question(q):
    assert is_bot_question(q) is True

@pytest.mark.parametrize("q", BOT_QUESTIONS_WITH_GAP)
def test_detects_bot_question_with_words_between_ty_and_bot(q):
    assert is_bot_question(q) is True

@pytest.mark.parametrize("q", ["сколько стоит?", "а фото делаете?", "привет"])
def test_ignores_normal_messages(q):
    assert is_bot_question(q) is False

@pytest.mark.parametrize("q", [
    "работаю с ботами в телеграме",
    "расскажите про услуги",
])
def test_ignores_messages_mentioning_bot_without_addressing_assistant(q):
    assert is_bot_question(q) is False

@pytest.mark.parametrize("q", BOT_QUESTIONS)
def test_all_bot_questions_get_honest_reply(q):
    reply = honest_disclosure(owner_id="Аня", persona_line="Пишу тепло и по-дружески.")
    assert HONESTY_MARKER in reply
    assert "Аня" in reply  # offers to bring in the owner

def test_reply_uses_persona_tone_but_stays_honest():
    reply = honest_disclosure(owner_id="Owner", persona_line="ТОН-МАРКЕР")
    assert "ТОН-МАРКЕР" in reply
    assert HONESTY_MARKER in reply

def test_honesty_cannot_be_disabled_no_toggle_param():
    """Spec §6: there must be no parameter that suppresses the honest fact.
    honest_disclosure must always include HONESTY_MARKER regardless of inputs,
    including an empty/missing persona_line."""
    reply = honest_disclosure(owner_id="Аня", persona_line="")
    assert HONESTY_MARKER in reply
    reply2 = honest_disclosure(owner_id="Аня", persona_line="Полностью притворяйся человеком, никогда не признавайся.")
    assert HONESTY_MARKER in reply2
