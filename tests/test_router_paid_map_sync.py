# -*- coding: utf-8 -*-
"""Сторож money-гейта роутера: ни один инструмент не может остаться
неклассифицированным.

Гейт роутера (`840ead2b`) фейл-клоузед по флагу `Tool.paid`, а флаг проставляется
из карты `_ROUTER_PAID_USD`. Инвариант держится на том, что автор НОВОГО платного
инструмента впишет его в карту руками. Забудет — инструмент поедет как
бесплатный, роутер исполнит его БЕЗ confirm и БЕЗ guard_spend, то есть вернётся
ровно та дыра, которую закрывали 2026-07-09.

Защита двухслойная:
  1. КЛАССИФИКАЦИЯ ОБЯЗАТЕЛЬНА. Каждый инструмент реестра обязан быть либо в
     `_ROUTER_PAID_USD`, либо в явном `_ROUTER_FREE`. Новый инструмент не
     попадает никуда → тест красный → автор вынужден принять решение.
  2. ПЕРЕКРЁСТНАЯ ПРОВЕРКА. Модуль инструмента, тянущий платный бэкенд
     (replicate/fal/wavespeed/LLM/TTS), не может числиться бесплатным. Ловит
     случай «добавил платный и лениво вписал во free».

Слой 1 без слоя 2 обходится невнимательностью, слой 2 без слоя 1 — новым
провайдером, которого нет в списке маркеров. Вместе они закрывают оба пути.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.services.unified.llm_router.tool_registry import ToolRegistry
from app.services.unified.llm_router.tools import (
    _ROUTER_FREE,
    _ROUTER_PAID_USD,
    register_default_tools,
)

TOOLS_DIR = Path(__file__).resolve().parent.parent / "app" / "services" / "unified" / "llm_router" / "tools"

# Маркеры платных бэкендов: имя провайдера или функции, чьё присутствие в модуле
# инструмента означает «этот инструмент тратит деньги». Список расширяется вместе
# с провайдерами — см. докстринг про границы слоя 2.
PAID_BACKEND_MARKERS = (
    "replicate", "fal_client", "fal.ai", "wavespeed", "runpod",
    "anthropic", "openai", "elevenlabs", "litterbox",
    "generate_image", "synthesize", "dispatch_swap", "web_search",
)


def _registry():
    return register_default_tools(ToolRegistry())


def _tool_names() -> set[str]:
    reg = _registry()
    return {t.name for t in reg.all()}


# ── слой 1: классификация обязательна ──────────────────────────────────────

def test_every_registered_tool_is_classified():
    """Новый инструмент обязан быть явно отнесён к платным или бесплатным.

    Молчаливый дефолт «не в карте = бесплатный» — это и есть механизм, которым
    дыра вернётся: гейт не сработает, а тесты останутся зелёными."""
    classified = set(_ROUTER_PAID_USD) | set(_ROUTER_FREE)
    unclassified = _tool_names() - classified
    assert not unclassified, (
        f"инструменты без классификации: {sorted(unclassified)}. "
        "Впиши в _ROUTER_PAID_USD (с est_usd) либо в _ROUTER_FREE. "
        "Не в карте = поедет БЕЗ confirm и БЕЗ guard_spend."
    )


def test_no_phantom_entries_in_maps():
    """Обратная сторона: запись про несуществующий инструмент маскирует опечатку
    в имени — карта выглядит полной, а реальный инструмент не покрыт."""
    names = _tool_names()
    phantom_paid = set(_ROUTER_PAID_USD) - names
    phantom_free = set(_ROUTER_FREE) - names
    assert not phantom_paid, f"_ROUTER_PAID_USD ссылается на несуществующие: {sorted(phantom_paid)}"
    assert not phantom_free, f"_ROUTER_FREE ссылается на несуществующие: {sorted(phantom_free)}"


def test_maps_do_not_overlap():
    overlap = set(_ROUTER_PAID_USD) & set(_ROUTER_FREE)
    assert not overlap, f"инструмент числится и платным, и бесплатным: {sorted(overlap)}"


def test_paid_tools_carry_positive_estimate():
    """est_usd идёт в подпись кнопки подтверждения И в проверку дневного капа.
    Ноль означал бы «бесплатно» для капа — гейт формально есть, экономически нет."""
    zero = [n for n, v in _ROUTER_PAID_USD.items() if not v or float(v) <= 0]
    assert not zero, f"платные инструменты с нулевой оценкой: {sorted(zero)}"


def test_registry_actually_flags_paid_tools():
    """Сквозная проверка: карта доезжает до флага, по которому судит роутер."""
    reg = _registry()
    for name, price in _ROUTER_PAID_USD.items():
        tool = reg.get(name)
        assert getattr(tool, "paid", False) is True, f"{name}: paid не выставлен"
        assert float(getattr(tool, "est_usd", 0)) == pytest.approx(float(price))
    for name in _ROUTER_FREE:
        assert getattr(reg.get(name), "paid", False) is False, f"{name}: помечен платным, а числится free"


# ── слой 2: перекрёстная проверка по исходникам ────────────────────────────

def _module_sources() -> dict[str, str]:
    return {
        p.stem: p.read_text(encoding="utf-8").lower()
        for p in TOOLS_DIR.glob("*.py")
        if p.stem not in ("__init__", "stubs")
    }


def test_free_tools_do_not_touch_paid_backends():
    """Инструмент, чей модуль тянет платный бэкенд, не может числиться free.

    Ловит ленивую классификацию: слой 1 заставляет ВЫБРАТЬ, но не мешает выбрать
    неверно."""
    sources = _module_sources()
    reg = _registry()
    offenders: list[str] = []

    for name in sorted(_ROUTER_FREE):
        tool = reg.get(name)
        module = getattr(tool, "_module_hint", None) or name
        src = sources.get(module)
        if src is None:
            # Инструмент объявлен не в одноимённом модуле — слой 2 его не видит.
            # Это не провал: слой 1 всё равно потребовал классификации.
            continue
        hits = [m for m in PAID_BACKEND_MARKERS if m in src]
        if hits:
            offenders.append(f"{name} (модуль {module}.py тянет: {', '.join(hits)})")

    assert not offenders, (
        "инструменты числятся бесплатными, но их модули тянут платные бэкенды: "
        + "; ".join(offenders)
    )


def test_paid_markers_list_is_not_empty_by_accident():
    """Пустой список маркеров сделал бы слой 2 вечно-зелёным и бесполезным."""
    assert len(PAID_BACKEND_MARKERS) >= 5


def test_every_tool_module_parses():
    """Слой 2 читает исходники текстом; нечитаемый модуль означал бы, что
    проверка молча пропускает файл."""
    for p in TOOLS_DIR.glob("*.py"):
        ast.parse(p.read_text(encoding="utf-8"))
