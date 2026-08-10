"""Прайс и сетка торга (спека §2.3, решение владельца 3).

Торг разрешён и многошаговый, но реализован ДИСКРЕТНОЙ СЕТКОЙ констант, а не
арифметикой. Причина не стилистическая: у готовых чисел нет ни одной болезни
вычисленных — они не режутся guardrail'ом `large_number` и не ждут политики
D v2. Пол — нижняя граница прайсовой вилки, ниже нельзя НИКОГДА (правило №6).

Валидатор здесь — сторож, а не документация: конфиг, разошедшийся с прайсом,
роняет СТАРТ. Иначе бот однажды назовёт цену, которой клиент нигде не публиковал
(риск 10.11), и узнаем мы об этом от клиента.
"""
from __future__ import annotations

from dataclasses import dataclass

from chatter.payments.money import MINOR_EXPONENT, Money, MoneyError, from_major

# `lower_bound` в списке нет намеренно: он единственный систематически
# производит возвраты (риск 10.7) и решением владельца исключён из спеки.
AMOUNT_SOURCES: tuple[str, ...] = ("price_upper", "price_exact_only", "ask_owner")


class PricingConfigError(Exception):
    """Конфиг цен непригоден. Всегда ошибка СТАРТА, никогда предупреждение."""


@dataclass(frozen=True)
class LadderStep:
    amount: Money
    scope_key: str      # ключ текста «що змінюється» — без него уступка молчалива


@dataclass(frozen=True)
class Position:
    position_id: str
    title: str
    currency: str
    low: Money          # нижняя граница вилки прайса == пол торга
    high: Money         # верхняя == стартовая цена при price_upper
    steps: tuple[LadderStep, ...]

    @property
    def top(self) -> LadderStep:
        return self.steps[0]

    @property
    def floor(self) -> LadderStep:
        return self.steps[-1]


@dataclass(frozen=True)
class Pricing:
    amount_source: str
    positions: dict[str, Position]


def step_at(pos: Position, idx: int) -> LadderStep:
    if not 0 <= idx < len(pos.steps):
        raise PricingConfigError(
            f"ступень {idx} вне сетки позиции {pos.position_id!r} "
            f"(всего {len(pos.steps)})")
    return pos.steps[idx]


def is_floor(pos: Position, idx: int) -> bool:
    return idx >= len(pos.steps) - 1


def next_step(pos: Position, idx: int) -> LadderStep | None:
    """Следующая ступень ВНИЗ, ровно одна за ход (§2.3). `None` — пол достигнут:
    это «зови владельца», а не «придумай число». Ниже пола нет ничего."""
    if is_floor(pos, idx):
        return None
    return step_at(pos, idx + 1)


def _money(raw, ccy: str, where: str) -> Money:
    try:
        return from_major(raw, ccy)
    except MoneyError as exc:
        raise PricingConfigError(f"{where}: {exc}") from exc


def _literal_in_knowledge(m: Money, ccy: str, knowledge: str) -> bool:
    """Число из конфига обязано встречаться в knowledge как ЛИТЕРАЛ."""
    from chatter.payments.money import format_major
    return format_major(m) in (knowledge or "")


def _load_position(pid: str, raw: dict, knowledge: str) -> Position:
    if not isinstance(raw, dict):
        raise PricingConfigError(f"позиция {pid!r}: ожидался словарь")

    ccy = raw.get("currency")
    if ccy not in MINOR_EXPONENT:
        raise PricingConfigError(
            f"позиция {pid!r}: currency={ccy!r} — нужен ISO-4217 из "
            f"{sorted(MINOR_EXPONENT)}, а не символ (§14 п.8)")

    rng = raw.get("price_range")
    if not (isinstance(rng, (list, tuple)) and len(rng) == 2):
        raise PricingConfigError(f"позиция {pid!r}: price_range обязан быть [низ, верх]")
    low = _money(rng[0], ccy, f"позиция {pid!r} price_range[0]")
    high = _money(rng[1], ccy, f"позиция {pid!r} price_range[1]")
    if low.minor > high.minor:
        raise PricingConfigError(f"позиция {pid!r}: price_range задан наоборот")

    # Правило №5 с технической гарантией: публичные границы обязаны быть в
    # knowledge. ПРОМЕЖУТОЧНЫЕ ступени — не обязаны: они внутренние
    # переговорные и в модель не попадают вовсе (§14 п.16).
    for bound, name in ((low, "нижняя"), (high, "верхняя")):
        if not _literal_in_knowledge(bound, ccy, knowledge):
            raise PricingConfigError(
                f"позиция {pid!r}: {name} граница вилки отсутствует в knowledge "
                f"как литерал — бот назвал бы цену, которой клиент не публиковал")

    raw_steps = raw.get("ladder")
    if not isinstance(raw_steps, (list, tuple)) or not raw_steps:
        raise PricingConfigError(f"позиция {pid!r}: ladder пуст — торговать нечем")

    steps: list[LadderStep] = []
    seen_scopes: set[str] = set()
    prev: int | None = None
    for i, s in enumerate(raw_steps):
        if not isinstance(s, dict):
            raise PricingConfigError(f"позиция {pid!r}, ступень {i}: ожидался словарь")
        amount = _money(s.get("amount"), ccy, f"позиция {pid!r}, ступень {i}")
        scope = s.get("scope_key")
        if not isinstance(scope, str) or not scope.strip():
            raise PricingConfigError(
                f"позиция {pid!r}, ступень {i}: пустой scope_key — уступка без "
                f"названного обмена это «молча дешевле» (риск 10.12)")
        if scope in seen_scopes:
            raise PricingConfigError(
                f"позиция {pid!r}, ступень {i}: scope_key {scope!r} повторяется — "
                f"цена вниз, а объём тот же")
        if prev is not None and amount.minor >= prev:
            raise PricingConfigError(
                f"позиция {pid!r}, ступень {i}: сетка обязана строго убывать")
        seen_scopes.add(scope)
        prev = amount.minor
        steps.append(LadderStep(amount, scope))

    if steps[0].amount != high:
        raise PricingConfigError(
            f"позиция {pid!r}: верх сетки {steps[0].amount.minor} ≠ верхней границе "
            f"вилки {high.minor} — политика price_upper назвала бы не ту цену")
    if steps[-1].amount != low:
        raise PricingConfigError(
            f"позиция {pid!r}: пол сетки {steps[-1].amount.minor} ≠ нижней границе "
            f"вилки {low.minor} — торг ушёл бы ниже опубликованного (правило №6)")

    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        raise PricingConfigError(f"позиция {pid!r}: пустой title")

    return Position(pid, title, ccy, low, high, tuple(steps))


def load_pricing(raw: dict, *, knowledge: str) -> Pricing:
    """Разобрать и ПРОВЕРИТЬ секцию `pricing` конфига клиента.

    Любое расхождение — исключение, а не значение по умолчанию: молчаливый
    дефолт на деньгах это класс бага (урок P17)."""
    if not isinstance(raw, dict):
        raise PricingConfigError("секция pricing обязана быть словарём")

    source = raw.get("amount_source", "price_upper")
    if source not in AMOUNT_SOURCES:
        raise PricingConfigError(
            f"amount_source={source!r}: допустимы {AMOUNT_SOURCES}. "
            f"lower_bound исключён решением владельца (риск 10.7)")

    raw_positions = raw.get("positions") or {}
    if not isinstance(raw_positions, dict):
        raise PricingConfigError("pricing.positions обязан быть словарём")

    positions = {
        pid: _load_position(pid, praw, knowledge)
        for pid, praw in raw_positions.items()
    }
    return Pricing(source, positions)
