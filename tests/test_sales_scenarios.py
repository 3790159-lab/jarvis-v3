# -*- coding: utf-8 -*-
"""Сторожа на НАБОР сценариев «продажности» (спека sales-competence §6.2).

Список файлов ЛИТЕРАЛЬНЫЙ, а не собранный glob-ом. Интроспекция врёт в обе
стороны: пропавший сценарий она не заметит (набор просто станет меньше),
а лишний — молча примет. Порог «8 из 10» имеет смысл только если десять
файлов на месте поимённо.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chatter.core.drill import EXPECT_KEYS, parse_scenario

SALES = Path(__file__).resolve().parents[1] / "docs" / "chatter" / "drills" / "sales"

EXPECTED = (
    "s01-nightclub-baseline.yaml",
    "s02-direct-task-sell-in-one.yaml",
    "s03-numbers-in-brief.yaml",
    "s04-price-objection-500.yaml",
    "s05-cases-with-figures.yaml",
    "s06-sla-and-fallback.yaml",
    "s07-guarantees-and-contract.yaml",
    "s08-language-mirror-ru.yaml",
    "s09-empathy-and-repeats.yaml",
    "s10-legit-escalation.yaml",
)


def test_the_set_is_exactly_ten_named_files():
    on_disk = sorted(p.name for p in SALES.glob("*.yaml"))
    assert on_disk == sorted(EXPECTED)


@pytest.mark.parametrize("name", EXPECTED)
def test_every_scenario_parses(name):
    """Битый сценарий = громкая ошибка. Молчаливый пропуск скрыл бы, что часть
    набора не выполнялась, а отчёт всё равно вышел бы зелёным."""
    scenario = parse_scenario((SALES / name).read_text(encoding="utf-8"))
    assert scenario.steps, name
    assert scenario.client == "volska"
    for step in scenario.steps:
        assert set(step.expect) <= EXPECT_KEYS


def test_control_scenario_demands_escalation():
    """s10 — ОБРАТНЫЙ. Если он не требует карточки, набор перестаёт ловить
    перекрученный маятник: бот, разучившийся звать владельца, пройдёт 10 из 10."""
    s10 = parse_scenario((SALES / "s10-legit-escalation.yaml").read_text(encoding="utf-8"))
    expect = s10.steps[0].expect
    assert expect.get("card_delivered") is True
    assert expect.get("obligations") == {"owner_write": "delivered"}


def test_sales_checks_are_actually_used_by_the_set():
    """Каждый новый ключ §6.1 обязан быть ЗАДЕЙСТВОВАН хотя бы одним сценарием.

    Ключ, который никто не просит, — мёртвый код в словаре: он выглядит
    проверкой, но не исполняется ни разу."""
    used = set()
    for name in EXPECTED:
        for step in parse_scenario((SALES / name).read_text(encoding="utf-8")).steps:
            used |= set(step.expect)
    new_keys = {
        "answers_before_escalating", "uses_lead_numbers", "offer_range",
        "questions_count", "next_step_with_sla", "no_escalation",
        "reply_language", "empathy_max", "no_repeated_formula",
    }
    assert new_keys <= used, sorted(new_keys - used)
