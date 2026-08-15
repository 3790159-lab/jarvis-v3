"""Годовые единицы времени в whitelist сроков (тот же класс, что P20 «день»).

`_TIME_UNIT` знает дни/недели/месяцы/часы и месяцы года, но НЕ знает года:
«рік/роки/років», «год/года/лет». Значит фрагмент knowledge вида
«Гарантія — 1 рік» не признаётся срочным, «1» не попадает в deadline_numbers,
и обеспеченный базой ответ «замінимо за 1 рік» подавляется целиком — ровно
инцидент P20, только на другой морфологии. Дыра общая: она бьёт и по Ольге
(строки договора/супроводу), и по любому клиенту с годовой гарантией.

⚠️ Граница слова обязательна и здесь (см. соседний test_guardrails_p20_stems):
наивный стем «год» матчит «годный», «рок» — «рок-гурт», «лет» — «летний»,
и ЦЕНЫ из таких строк уехали бы в срочное множество, обеспечивая выдуманные
сроки.
"""
from __future__ import annotations

import pytest

from chatter.core.guardrails import _context_numbers, contains_unbacked_claim


@pytest.mark.parametrize("frag,num", [
    ("- Супровід — 1 рік", "1"),
    ("- Гарантія на вироби — 2 роки", "2"),
    ("- Договір — від 3 років", "3"),
    ("- Працюємо на ринку 7 років", "7"),
    ("- Гарантия — 1 год", "1"),
    ("- Сотрудничество — 2 года", "2"),
    ("- Опыт команды — 5 лет", "5"),
])
def test_year_units_are_recognised(frag, num):
    _, deadline = _context_numbers(frag)
    assert num in deadline, f"{frag!r}: {num} не попало в deadline"


@pytest.mark.parametrize("frag", [
    "- Оформлення для рок-гурту — 400 $",
    "- 3 летних макета — 250 $",
    "- Афіша для балету — 300 $",
    "- Пакет годный к печати — 200 $",
])
def test_year_lookalikes_do_not_leak_into_deadline(frag):
    """Грабля \\b: «рок-гурт», «летних», «балету», «годный» содержат стемы года,
    но единицами времени не являются. Цены не имеют права попадать в срочное."""
    price, deadline = _context_numbers(frag)
    assert price, f"{frag!r}: цены обязаны попасть в price"
    assert not deadline, f"{frag!r}: цены протекли в deadline: {deadline}"


def test_backed_year_deadline_is_not_suppressed():
    """Главный симптом дыры: срок ЕСТЬ в базе, а ответ всё равно подавлен."""
    knowledge = "# Терміни\n- Гарантія на вироби — 1 рік\n- Пакування — 300 $"
    assert contains_unbacked_claim("Замінимо за 1 рік, це в гарантії.",
                                   knowledge) is False


def test_invented_year_deadline_is_still_caught():
    """Защита НЕ ослабла: год, которого в базе нет, ловится и после фикса."""
    knowledge = "# Терміни\n- Гарантія на вироби — 1 рік\n- Пакування — 300 $"
    assert contains_unbacked_claim("Зробимо за 9 років.", knowledge) is True


def test_bare_year_claim_without_knowledge_is_unbacked():
    """Безцифровое «через рік» на базе, где о годах ни слова, — выдумка."""
    knowledge = "# Ціни\n- Пакування — 300 $\n- Логотип — 400 $"
    assert contains_unbacked_claim("Оновимо через рік.", knowledge) is True


def test_bare_year_claim_is_backed_when_knowledge_mentions_years():
    knowledge = "# Терміни\n- Гарантія на вироби — 1 рік"
    assert contains_unbacked_claim("Оновимо через рік.", knowledge) is False
