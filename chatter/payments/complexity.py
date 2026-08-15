"""Гейт эскалации — СЛОЖНОСТЬ, а не сумма (спека §2.4, решение владельца 2).

Порог по сумме зовёт владельца там, где ошибается калькулятор. Но бот с
готовыми числами из прайса в арифметике не ошибается — он ошибается в ПОНИМАНИИ
запроса. Поэтому гейт стоит на структуре запроса.

Разделение ответственности жёсткое: модель ФИКСИРУЕТ факты (какие позиции
узнаны, сколько единиц, что не опознано, есть ли пометка объёма/срока), вывод
«просто/сложно» делает КОД. Модель не решает, звать ли человека.

Дефолт закрыт ОТКАЗОМ (урок P17, `owed_by` для kind=other): нет разбора — зовём
владельца. Молчаливый оптимистичный дефолт на деньгах — класс бага.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from chatter.payments.pricing import Pricing

# Причины эскалации. Перечисление закрытое: карточка владельцу рендерится по
# ключу, а не по свободному тексту.
REASONS: tuple[str, ...] = (
    "no_parse",                # разбор не удался / пустой запрос
    "multiple_services",       # больше одной услуги
    "multiple_units",          # одна услуга, но единиц больше одной
    "unknown_service",         # услуги нет в прайсе
    "unknown_position",        # ключ позиции не найден в конфиге (рассинхрон)
    "unknown_tier",            # объём назван, но такой ступени у позиции нет
    "bad_quantity",            # количество ≤ 0 — не «наверное один»
    "volume_out_of_norm",      # объём вне обычного
    "deadline_out_of_norm",    # срок вне обычного
)


@dataclass(frozen=True)
class RequestedItem:
    position_id: str | None
    qty: int
    raw: str = ""              # как это звучало у лида — для карточки владельцу
    # Выбранная ПУБЛИЧНАЯ ступень объёма (решение владельца 12.08). None —
    # «объём не назван», и это рабочий случай: политика price_upper с оговоркой,
    # как до ярусов. Идентификатор, а не текст: ступень едет в счёт.
    tier_id: str | None = None


@dataclass(frozen=True)
class QuoteRequest:
    """Факты, зафиксированные классификатором. Никаких выводов."""
    items: tuple[RequestedItem, ...] = ()
    unknown_services: tuple[str, ...] = ()
    volume_note: str | None = None
    deadline_note: str | None = None
    parsed: bool = True        # False = разбор не состоялся (сбой/пусто)


@dataclass(frozen=True)
class Simple:
    """Простая одиночная услуга: бот считает и называет сумму сам."""
    position_id: str
    qty: int = 1
    # Какой объём выбран. None — не выбран, счёт пойдёт по политике price_upper.
    tier_id: str | None = None


@dataclass(frozen=True)
class NeedsOwner:
    """Зовём владельца. Причины — ВСЕ применимые, в детерминированном порядке:
    карточка с одной причиной из трёх вводит в заблуждение сильнее, чем её
    отсутствие."""
    reasons: tuple[str, ...] = field(default=())


def _ordered(found: list[str]) -> tuple[str, ...]:
    """Порядок — по объявлению в REASONS, а не по порядку ОБНАРУЖЕНИЯ: карточка
    владельцу и тесты не должны зависеть от того, в каком порядке лид перечислил
    услуги. Заодно снимает дубли.

    `found` — список, а не множество, намеренно: у множества порядок обхода
    зависит от хешей, и тест, сравнивающий два прогона внутри одного процесса,
    оказался бы слепым к потере сортировки (поймано мутацией DEV-26)."""
    return tuple(r for r in REASONS if r in found)


def assess_complexity(req: QuoteRequest, pricing: Pricing) -> Simple | NeedsOwner:
    """Чистая функция. Возвращает ровно одну из двух форм — вызыватель не должен
    уметь получить None или строку и додумать за неё."""
    found: list[str] = []

    if not req.parsed or not req.items:
        # Пустой разбор — это не «клиент ничего не просил», это «мы не поняли».
        found.append("no_parse")

    if len(req.items) > 1:
        found.append("multiple_services")

    for item in req.items:
        if item.qty > 1:
            found.append("multiple_units")
        elif item.qty < 1:
            found.append("bad_quantity")
        if item.position_id is None:
            found.append("unknown_service")
        elif item.position_id not in pricing.positions:
            found.append("unknown_position")
        # Ступень названа, но её нет в конфиге (или у позиции ярусов нет вовсе)
        # — разбор разошёлся с прайсом. Подставить вместо неузнанной ступени
        # верхнюю значило бы выставить счёт за объём, которого лид не выбирал.
        elif (item.tier_id is not None
              and pricing.positions[item.position_id].tier(item.tier_id) is None):
            found.append("unknown_tier")

    if req.unknown_services:
        found.append("unknown_service")
    if req.volume_note:
        found.append("volume_out_of_norm")
    if req.deadline_note:
        found.append("deadline_out_of_norm")

    if found:
        return NeedsOwner(_ordered(found))

    only = req.items[0]
    # position_id уже проверен выше; сузить тип для читателя.
    assert only.position_id is not None
    return Simple(only.position_id, only.qty, tier_id=only.tier_id)
