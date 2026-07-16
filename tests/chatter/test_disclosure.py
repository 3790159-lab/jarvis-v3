from __future__ import annotations
import pytest
from chatter.core.disclosure import (
    is_bot_question, honest_disclosure, HONESTY_MARKER, HONESTY_MARKERS,
)

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

# EN phrasings with words BETWEEN the address ("are you" / "am I talking to"
# / "is this") and the target (bot/human/robot/ai/real person) -- mirrors
# BOT_QUESTIONS_WITH_GAP above but for English, since demo2 is English and
# shown to real people.
EN_BOT_QUESTIONS_WITH_GAP = [
    "are you a bot?",
    "are you actually a bot?",
    "are you really a bot?",
    "are you for real a human?",
    "are you a real human?",
    "are you even human?",
    "are you an AI?",
    "so, are you a bot or a real person?",
    "is this actually a bot?",
    "am I talking to a bot?",
    "am I chatting with a real person or a bot?",
]

EN_NOT_BOT_QUESTIONS = [
    "I need a bot for my telegram channel",
    "do you build bots?",
    "how much is the automation bot?",
    "are you available tomorrow?",
    "is this the real price?",
    "am I the right person to ask about pricing?",
    "tell me about your services",
    "can a human review this later?",
]

@pytest.mark.parametrize("q", EN_BOT_QUESTIONS_WITH_GAP)
def test_detects_en_bot_question_with_words_in_between(q):
    assert is_bot_question(q) is True

@pytest.mark.parametrize("q", EN_NOT_BOT_QUESTIONS)
def test_ignores_en_messages_mentioning_bot_human_without_addressing_assistant(q):
    assert is_bot_question(q) is False

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


def test_default_language_is_ru_and_owner_name_grammar_is_correct():
    """Regression: the RU template used to render 'позову Дмитрий лично' --
    wrong grammatical case regardless of the owner's name. The owner name must
    now sit in apposition (nominative), e.g. '... владельца — Дмитрий ответит
    лично.', which reads correctly for any name."""
    reply = honest_disclosure(owner_id="Дмитрий", persona_line="")
    assert HONESTY_MARKER in reply
    assert "— Дмитрий ответит лично" in reply


def test_honest_disclosure_english():
    reply = honest_disclosure(owner_id="Alex", persona_line="", language="en")
    assert HONESTY_MARKERS["en"] in reply
    assert "Alex" in reply
    assert HONESTY_MARKER not in reply  # not the RU marker


def test_honest_disclosure_ukrainian():
    reply = honest_disclosure(owner_id="Олена", persona_line="", language="uk")
    assert HONESTY_MARKERS["uk"] in reply
    assert "Олена" in reply


def test_honest_disclosure_unknown_language_falls_back_to_ru():
    reply = honest_disclosure(owner_id="Аня", persona_line="", language="fr")
    assert HONESTY_MARKER in reply
