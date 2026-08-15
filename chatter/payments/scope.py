"""Тексты объёма на ступенях торга (§2.3) и их заглушки.

Каждая ступень сетки обязана нести текст «що змінюється»: уступка без
названного обмена — это скидка без встречного сокращения (риск 10.12).

Реальные формулировки задаёт клиент. До их получения ступени несут ЗАГЛУШКИ с
явной пометкой `placeholder: true`, и такой конфиг обслуживает ТОЛЬКО
дрил-контакты — тот же гейт, что у test-реквизитов, по той же причине.

Пометка только явная. Угадывание по виду строки («похоже на TODO») сделало бы
валидатор непредсказуемым в обе стороны: и пропускало бы заглушки без маркера,
и роняло бы живой текст, случайно похожий на служебный.
"""
from __future__ import annotations

from dataclasses import dataclass

from chatter.payments.drill_gate import guard_test_asset
from chatter.payments.pricing import Pricing


class ScopeConfigError(Exception):
    """Текста объёма нет, он пуст или ключ неизвестен."""


@dataclass(frozen=True)
class ScopeText:
    text: str
    placeholder: bool = False


def load_scope_texts(raw: dict) -> dict[str, ScopeText]:
    if not isinstance(raw, dict):
        raise ScopeConfigError("scope_texts обязан быть словарём")
    out: dict[str, ScopeText] = {}
    for key, entry in raw.items():
        if isinstance(entry, str):
            text, placeholder = entry, False
        elif isinstance(entry, dict):
            text = entry.get("text") or ""
            placeholder = bool(entry.get("placeholder", False))
        else:
            raise ScopeConfigError(f"{key!r}: ожидалась строка или словарь с text")
        if not isinstance(text, str) or not text.strip():
            raise ScopeConfigError(
                f"{key!r}: пустой текст объёма — ступень без названного обмена "
                f"это «молча дешевле» (риск 10.12)")
        out[key] = ScopeText(text.strip(), placeholder)
    return out


def resolve_scope_text(scope_key: str, texts: dict[str, ScopeText], *,
                       contact_id: str) -> str:
    """Текст обмена для ступени. Заглушка — только дрил-контакту."""
    entry = texts.get(scope_key)
    if entry is None:
        raise ScopeConfigError(
            f"нет текста объёма для ступени {scope_key!r}: уступить эту ступень "
            f"нечем — обмен назвать невозможно")
    if entry.placeholder:
        guard_test_asset(contact_id=contact_id, what=f"заглушка объёма {scope_key!r}")
    return entry.text


def assert_pricing_usable(pricing: Pricing, texts: dict[str, ScopeText], *,
                          contact_id: str) -> None:
    """Проверить весь прайс ДО разговора: все ступени имеют тексты, и ни одна
    заглушка не обслуживает живой контакт.

    Почему до разговора, а не в момент уступки: обнаружить заглушку на третьем
    шаге торга — значит оборвать диалог в самом дорогом месте. Названы
    сразу ВСЕ проблемные ступени, а не первая: чинить по одной — это столько же
    заходов, сколько ступеней."""
    missing: list[str] = []
    stubs: list[str] = []
    for pos in pricing.positions.values():
        for step in pos.steps:
            entry = texts.get(step.scope_key)
            if entry is None:
                missing.append(f"{pos.position_id}:{step.scope_key}")
            elif entry.placeholder:
                stubs.append(f"{pos.position_id}:{step.scope_key}")

    if missing:
        raise ScopeConfigError(
            "ступени без текста объёма: " + ", ".join(missing))
    if stubs:
        # Гейт — единственная точка решения «можно ли тестовый актив этому
        # контакту»; для дрил-контакта он молча пропустит.
        guard_test_asset(
            contact_id=contact_id,
            what="заглушки объёма на ступенях [" + ", ".join(stubs) + "]")
