"""Книга ПУБЛИЧНЫХ описаний объёма — «що входить» в ступень (решение владельца
12.08).

Отдельный модуль, а не секция `scope.py`, и разделение структурное — свой тип,
своя ошибка, свои функции. Причина та же, что у двух книг реквизитов: смешать
две книги можно было бы опиской в одном ключе, а цена ошибки здесь —
опубликованный внутренний текст.

Что различается по существу:

  · `scope_texts` — «що ЗМІНЮЄТЬСЯ», когда бот уступает ступень сетки. Это
    дельта скидочной лестницы, и вслух она не произносится никогда.
  · `tier_texts` — «що ВХОДИТЬ» в публичную ступень объёма. Бот называет их
    открыто, вместе с ценой: цена без названного объёма — это цена ни за что.

Конвенция заглушек — та же, что у `scope.py`, и по той же причине: пометка
`placeholder: true` ЯВНАЯ, угадывание по виду строки сделало бы валидатор
непредсказуемым в обе стороны. Пока пометка стоит, ступень обслуживает ТОЛЬКО
дрил-контакты — тот же гейт, что у тестовых реквизитов.
"""
from __future__ import annotations

from dataclasses import dataclass

from chatter.payments.drill_gate import guard_test_asset
from chatter.payments.pricing import Pricing


class TierTextsError(Exception):
    """Публичного описания объёма нет, оно пусто или ключ неизвестен."""


@dataclass(frozen=True)
class TierText:
    text: str
    placeholder: bool = False


def load_tier_texts(raw: dict) -> dict[str, TierText]:
    if not isinstance(raw, dict):
        raise TierTextsError("tier_texts обязан быть словарём")
    out: dict[str, TierText] = {}
    for key, entry in raw.items():
        if isinstance(entry, str):
            text, placeholder = entry, False
        elif isinstance(entry, dict):
            text = entry.get("text") or ""
            placeholder = bool(entry.get("placeholder", False))
        else:
            raise TierTextsError(
                f"{key!r}: ожидалась строка или словарь с text")
        if not isinstance(text, str) or not text.strip():
            raise TierTextsError(
                f"{key!r}: пустое описание объёма — цена без названного объёма "
                f"это цена ни за что")
        out[key] = TierText(text.strip(), placeholder)
    return out


def resolve_tier_text(tier_text_key: str, texts: dict[str, TierText], *,
                      contact_id: str) -> str:
    """Публичный текст ступени. Заглушка — только дрил-контакту."""
    entry = texts.get(tier_text_key)
    if entry is None:
        raise TierTextsError(
            f"нет описания объёма для ступени {tier_text_key!r}: назвать цену "
            f"нечем — лид не поймёт, за что платит")
    if entry.placeholder:
        guard_test_asset(contact_id=contact_id,
                         what=f"заглушка объёма {tier_text_key!r}")
    return entry.text


def assert_tier_texts_usable(pricing: Pricing, texts: dict[str, TierText], *,
                             contact_id: str) -> None:
    """Проверить ВСЕ ярусы прайса ДО разговора.

    Позиции без ярусов книгу не требуют вовсе: ступени заводятся только там, где
    объём различим, и требовать описания от остальных значило бы сделать ярусы
    обязательными для всех.

    Названы сразу ВСЕ проблемные ступени, а не первая — чинить по одной столько
    же заходов, сколько ступеней."""
    missing: list[str] = []
    stubs: list[str] = []
    for pos in pricing.positions.values():
        for tier in pos.tiers:
            entry = texts.get(tier.tier_text_key)
            if entry is None:
                missing.append(f"{pos.position_id}:{tier.tier_text_key}")
            elif entry.placeholder:
                stubs.append(f"{pos.position_id}:{tier.tier_text_key}")

    if missing:
        raise TierTextsError(
            "ступени объёма без публичного описания: " + ", ".join(missing))
    if stubs:
        # Гейт — единственная точка решения «можно ли тестовый актив этому
        # контакту»; для дрил-контакта он молча пропустит.
        guard_test_asset(
            contact_id=contact_id,
            what="заглушки описаний объёма [" + ", ".join(stubs) + "]")
