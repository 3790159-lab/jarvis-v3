"""P20 (б)+(в): состояние «ждём владельца» и анти-самоповтор.

Дефект (а) инцидента 2026-07-29: `suppressed_fallback` — чистая константа, и на
трёх подавлениях подряд лид получил БАЙТ-В-БАЙТ одну строку (msg 318/320/322).
Причём msg 320 был ответом на прямой вопрос «вы уточнили детали?)» — фразой
«уточню деталі та повернуся». Бот уже передал вопрос керівниці; повторять
«уточню» — ложь.
"""
from __future__ import annotations

import pytest

from chatter.core.escalation import (
    awaiting_owner_fallback, pick_non_repeating, suppressed_fallback,
)


@pytest.mark.parametrize("language", ["ru", "uk", "en"])
def test_awaiting_owner_differs_from_generic_stub(language):
    generic = suppressed_fallback(language=language, owner_ref=None)
    awaiting = awaiting_owner_fallback(language=language, owner_ref=None)
    assert awaiting != generic


def test_awaiting_owner_states_handoff_already_happened_uk():
    """Смысловой контракт: «вже передала», а не «уточню» — иначе это ложь."""
    text = awaiting_owner_fallback(language="uk", owner_ref="керівниці")
    assert "вже переда" in text.lower()
    assert "уточню" not in text.lower()


def test_awaiting_owner_promises_to_write_back():
    text = awaiting_owner_fallback(language="uk", owner_ref="керівниці")
    assert "напишу" in text.lower()


def test_unknown_language_falls_back_to_ru_not_keyerror():
    assert awaiting_owner_fallback(language="zz", owner_ref=None)


# ---------------------------------------------------------------- анти-повтор

def test_candidate_passes_when_it_differs():
    assert pick_non_repeating("Б", previous="А", variants=()) == "Б"


def test_identical_candidate_is_replaced_by_a_variant():
    got = pick_non_repeating("А", previous="А", variants=("Б", "В"))
    assert got == "Б"


def test_variant_equal_to_previous_is_skipped():
    got = pick_non_repeating("А", previous="Б", variants=("Б", "В"))
    assert got == "А", "кандидат отличается от предыдущего — вариант не нужен"
    got = pick_non_repeating("Б", previous="Б", variants=("Б", "В"))
    assert got == "В"


def test_no_previous_message_means_candidate_is_fine():
    assert pick_non_repeating("А", previous=None, variants=()) == "А"


def test_repeat_differing_only_by_whitespace_or_case_is_still_a_repeat():
    """«Байт-идентичная» трактуется строже байта: лид видит один и тот же
    текст, лишний пробел этого не меняет."""
    assert pick_non_repeating("Привіт  ", previous="привіт", variants=()) is None


def test_norm_reply_absorbs_humanizer_typography():
    """Сторож шва: анти-самоповтор сравнивает СЫРОЙ кандидат с УЖЕ отправленным
    текстом, а перед отправкой его правит `humanize_typography` (em-dash → «-»,
    срез «!»). Если она начнёт менять что-то ещё, дубль снова начнёт проезжать —
    этот тест ловит расхождение, а не полагается на разовый аудит (DEV-19)."""
    from chatter.core.escalation import _norm_reply
    from chatter.core.humanizer import humanize_typography

    for raw in (
        suppressed_fallback(language="ru", owner_ref=None),
        suppressed_fallback(language="uk", owner_ref="керівницею"),
        awaiting_owner_fallback(language="uk", owner_ref="керівниці"),
        "Хороший вопрос — уточню детали и вернусь к вам.",
        "Вітаю! Розкажіть про задачу.",
    ):
        assert _norm_reply(humanize_typography(raw)) == _norm_reply(raw), (
            f"humanizer меняет текст так, что анти-повтор его не узнаёт: {raw!r}")


def test_returns_none_when_everything_repeats():
    """Честная пауза: вариантов нет — лучше промолчать ход, чем прислать
    третью копию. Владелец уже уведомлён карточкой."""
    assert pick_non_repeating("А", previous="А", variants=("А",)) is None
