"""P20 (а): whitelist сроков слепнет на форме «день»/«тиждень».

Корень инцидента 2026-07-28/29 (volska): knowledge.md содержит
«Маркетингова стратегія — 21 календарний день», но `_TIME_UNIT` построен на
русской беглой гласной (`дн(?:я|ей|ь)?\\w*`) и форму «день» НЕ матчит. Фрагмент
не признан срочным → «21» не попало в deadline_numbers → весь ответ с ценами
подавлен.

Второй контракт: стемы обязаны быть якорены на границу слова. Наивный «ден»
матчит «ве-ДЕН-ня» и «ай-ДЕН-тики» и утаскивает ЦЕНЫ (700/750/900) в срочное
множество, ослабляя гардрейл.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chatter.core.guardrails import _context_numbers, contains_unbacked_claim

VOLSKA_KNOWLEDGE = (
    Path(__file__).resolve().parents[2]
    / "chatter" / "clients" / "volska" / "knowledge.md"
)


def test_kalendarnyi_den_lands_in_deadline_numbers():
    """«21 календарний день» — это СРОК. Форма «день» обязана распознаваться."""
    _, deadline = _context_numbers(
        "- Маркетингова стратегія — 21 календарний день, далі виходимо на дзвінок")
    assert "21" in deadline


@pytest.mark.parametrize("frag,num", [
    ("- Стратегія — 21 календарний день", "21"),
    ("- Аудит — 1 робочий день", "1"),
    ("- Контент-план — 1 тиждень", "1"),
    ("- Супровід — від 2 місяців", "2"),
    ("- Правки — 3 тижні", "3"),
])
def test_ukrainian_time_units_are_recognised(frag, num):
    _, deadline = _context_numbers(frag)
    assert num in deadline, f"{frag!r}: {num} не попало в deadline"


@pytest.mark.parametrize("frag", [
    "- SMM-ведення — 750–900 $",
    "- Створення айдентики (лого, шрифти, брендгайд) — 700–900 $",
])
def test_price_words_containing_den_do_not_leak_into_deadline(frag):
    """Грабля \\b: «ведення»/«айдентики» содержат «ден», но это НЕ единица
    времени. Цены не имеют права попадать в срочное множество."""
    price, deadline = _context_numbers(frag)
    assert price, f"{frag!r}: цены обязаны попасть в price"
    assert not deadline, f"{frag!r}: цены протекли в deadline: {deadline}"


def test_bare_unit_claim_is_not_backed_by_a_substring_match():
    """Та же грабля \\b на ВТОРОЙ стороне: правило безцифрового срока проверяет
    «упомянута ли единица в knowledge» подстрокой. Стем «ден» сидит внутри
    «ведення», и без границы слова выдуманное «через день» считалось бы
    обеспеченным базой, где о днях не сказано ни слова."""
    knowledge = "- SMM-ведення — 750–900 $\n- Створення айдентики — 700–900 $"
    assert contains_unbacked_claim("Зробимо через день.", knowledge) is True


def test_every_deadline_number_in_real_knowledge_is_backed():
    """Сторож: КАЖДОЕ число из раздела «Стандартні терміни виконання» реального
    knowledge.md обязано попасть в deadline_numbers. Именно этот сторож поймал бы
    P20 молча и сразу, и ловит будущие клиентские базы с другой морфологией."""
    text = VOLSKA_KNOWLEDGE.read_text(encoding="utf-8")
    section = text.split("# Стандартні терміни виконання")[1].split("\n# ")[0]
    _, deadline = _context_numbers(text)
    import re
    missing = [
        n for n in {re.sub(r"\s", "", m.group()) for m in re.finditer(r"\d+", section)}
        if n not in deadline
    ]
    assert not missing, f"числа сроков не попали в deadline_numbers: {sorted(missing)}"


def test_live_suppressed_reply_passes_after_fix():
    """Живой ответ, подавленный 2026-07-29 01:14 (лид не увидел ни одной цены).
    Все четыре цены обеспечены knowledge, «21» — тоже, просто не был виден."""
    reply = (
        "Добрий вечір 🙂 Так, розповім одразу - у нас два основні напрямки в "
        "маркетингу: SMM-ведення (750–900 $, контент-план на тиждень наперед, "
        "узгоджуємо кожного четверга-п'ятниці) і розробка маркетингової "
        "стратегії (600–800 $, термін 21 календарний день, потім презентуємо "
        "результат і збираємо фідбек).\n\nЩо вам ближче?"
    )
    knowledge = VOLSKA_KNOWLEDGE.read_text(encoding="utf-8")
    assert contains_unbacked_claim(reply, knowledge) is False


def test_invented_deadline_is_still_caught():
    """Защита НЕ ослабла: срок, которого в базе НЕТ, ловится и после фикса.

    ⚠️ Число берём заведомо отсутствующее (99). «3 календарних дні» здесь НЕ
    годится: whitelist число-ориентирован, а «3» законно лежит в срочном
    множестве («Створення презентації — 3 робочі дні»), т.е. такой срок
    обеспечен по построению."""
    knowledge = VOLSKA_KNOWLEDGE.read_text(encoding="utf-8")
    assert contains_unbacked_claim("Стратегія 600–800 $, зробимо за 99 днів.",
                                   knowledge) is True
