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

# UK phrasings -- the product's stated market is Ukrainian. The RU adjacency
# patterns key on "ты"; Ukrainian "ти" never matches them, so a lead asking
# "ти бот?" slipped the honesty guarantee entirely and fell through to the LLM,
# whose prompt carries no honesty instruction (audit 2026-07-18, finding H3).
UK_BOT_QUESTIONS = [
    "ти бот?",
    "це бот?",
    "ти робот?",
    "ти жива людина?",
    "ти справжня?",
    "ти реальна людина?",
    "з ким я говорю, з ботом?",
    "ти людина чи бот?",
    "ти часом не бот?",
    "а ти взагалі бот?",
    "ти штучний інтелект?",
    "ти ші?",
]

UK_NOT_BOT_QUESTIONS = [
    "продаю ботів у телеграмі",
    "скільки коштує бот для розсилки?",
    "розкажіть про послуги",
    "ти вільна завтра?",
    "яка ціна на зйомку?",
]


@pytest.mark.parametrize("q", UK_BOT_QUESTIONS)
def test_detects_uk_bot_question(q):
    assert is_bot_question(q) is True


@pytest.mark.parametrize("q", UK_NOT_BOT_QUESTIONS)
def test_ignores_uk_messages_not_addressing_the_assistant(q):
    assert is_bot_question(q) is False


@pytest.mark.parametrize("q", UK_BOT_QUESTIONS)
def test_uk_bot_question_gets_honest_uk_disclosure(q):
    # Detection must lead to the honest UK disclosure (the whole point of H3):
    # the marker "я — віртуальний асистент" reaches a Ukrainian lead.
    assert is_bot_question(q) is True
    reply = honest_disclosure(owner_id="Олена", persona_line="", language="uk")
    assert HONESTY_MARKERS["uk"] in reply


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


def test_disclosure_reads_as_one_natural_phrase_not_two_glued_templates():
    """Regression (live run): the persona bio and the honesty template were
    concatenated as 'bio. honesty', which produced a lowercase 'я' right after a
    sentence period ('...по личному бренду. я — виртуальный ассистент...') and
    read as two intros butted together. The seam must now flow as one phrase --
    without touching the honest FACT itself."""
    bio = "Меня зовут Аня, мне 29. Я фотограф и консультант по личному бренду."
    reply = honest_disclosure(owner_id="Дмитрий", persona_line=bio)

    assert HONESTY_MARKER in reply                    # the honest fact is preserved
    assert reply.count("виртуальный ассистент") == 1  # not duplicated
    assert "бренду. я" not in reply                   # the specific glued seam is gone
    assert ". я —" not in reply                       # no lowercase 'я' after a period anywhere
    # the bio's trailing period is absorbed into a flowing connective, not butted
    assert "бренду." not in reply


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


# ---------------------------------------------------------------------------
# Онбординг-дырка №0: первая строка persona.md уходит в честный ответ ДОСЛОВНО.
# Клиент, пишущий persona.md как нормальный markdown ("# Аня"), ломал главный
# инвариант продукта: лид получал "# Аня — честно говоря, я — виртуальный
# ассистент". Демо-персона начинается с прозы, поэтому 615 тестов молчали.
# ---------------------------------------------------------------------------
import pytest

from chatter.run import _persona_first_line

_MARKDOWN_FIRST_LINES = [
    ("# Аня", "Аня"),
    ("## Персона: Аня, 26 лет", "Персона: Аня, 26 лет"),
    ("### Кто я", "Кто я"),
    ("- Менеджер студии ACME", "Менеджер студии ACME"),
    ("* Менеджер студии ACME", "Менеджер студии ACME"),
    ("+ Менеджер студии ACME", "Менеджер студии ACME"),
    ("> Я Аня, консультант", "Я Аня, консультант"),
    ("1. Я Аня, консультант", "Я Аня, консультант"),
]


@pytest.mark.parametrize("raw,expected", _MARKDOWN_FIRST_LINES)
def test_persona_first_line_strips_markdown_syntax(raw, expected):
    assert _persona_first_line(raw) == expected


def test_persona_first_line_skips_front_matter_and_headings():
    """Клиент вполне может начать файл с front-matter и заголовка. Берём
    первую СОДЕРЖАТЕЛЬНУЮ строку, а не первую непустую."""
    persona = "---\ntitle: persona\n---\n\n# Аня\n\nМеня зовут Аня, мне 26. Я консультант.\n"
    assert _persona_first_line(persona) == "Меня зовут Аня, мне 26. Я консультант."


def test_persona_first_line_falls_back_to_heading_when_no_prose():
    """Если содержательной прозы нет вовсе — заголовок лучше пустоты, но
    ОЧИЩЕННЫЙ от разметки."""
    assert _persona_first_line("# Аня\n") == "Аня"


def test_persona_first_line_keeps_prose_untouched():
    """Регрессия: демо-персона (проза) не должна измениться."""
    line = "Меня зовут Аня, мне 29. Я фотограф и консультант по личному бренду."
    assert _persona_first_line(line) == line


@pytest.mark.parametrize("raw,_expected", _MARKDOWN_FIRST_LINES)
def test_disclosure_never_leaks_markdown_syntax(raw, _expected):
    """Главный инвариант: что бы клиент ни написал первой строкой, честный
    ответ лиду не начинается с markdown-мусора."""
    reply = honest_disclosure(
        owner_id="Дмитрий", persona_line=_persona_first_line(raw), language="ru")
    assert HONESTY_MARKER in reply
    assert not reply.lstrip().startswith(("#", "-", "*", "+", ">", "1.", "---"))
