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
class Tier:
    """ПУБЛИЧНАЯ ступень объёма (решение владельца 12.08).

    Ровно ОДНА цена, названная лиду вслух вместе с описанием объёма. Ни вилки,
    ни пола, ни сетки внутри: дешевле — это меньший ОБЪЁМ, а не тихая скидка за
    тот же. Поэтому «уступка без названного обмена» (риск 10.12) тут невозможна
    по построению, а не удерживается проверкой.

    Отличие от `LadderStep` не в полях, а в публичности: ступень объёма
    произносится, шаг сетки — никогда."""
    id: str
    amount: Money
    tier_text_key: str      # ключ текста «що входить» в книге tier_texts
    # Слова КЛИЕНТА, которыми он выбирает объём («базовий», «повний»). Пусто —
    # ступень не выбирается словами НИКОГДА, и такой запрос уходит владельцу.
    # Тот же контракт, что у алиасов позиции: угаданный объём — это счёт за не
    # ту работу, только на ступень мельче.
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Position:
    position_id: str
    title: str
    currency: str
    low: Money          # нижняя граница вилки прайса == пол торга
    high: Money         # верхняя == стартовая цена при price_upper
    steps: tuple[LadderStep, ...]
    # Слова КЛИЕНТА, по которым позицию узнаёт предпасс (`intent.py`). Пусто —
    # позиция не узнаётся никогда, и запрос про неё уходит владельцу как
    # неразобранный. Это рабочий исход, а не поломка: лучше молча позвать
    # человека, чем угадать позицию и назвать цену за не ту работу.
    aliases: tuple[str, ...] = ()
    # Публичные ступени объёма. Пусто — позиция работает как до 12.08: одна
    # вилка и внутренняя сетка торга. Непусто — сетки нет вовсе (валидатор их
    # не пускает вместе), и цена называется выбором объёма.
    tiers: tuple[Tier, ...] = ()

    @property
    def top(self) -> LadderStep:
        """Стартовое предложение при невыбранном объёме (политика price_upper).

        У ярусной позиции это САМАЯ ДОРОГАЯ ступень, а не верх несуществующей
        вилки. Тип общий намеренно: вызывающей стороне (`dialogue`) не нужно
        знать, ярусная позиция или нет, — ей нужны сумма и ключ текста."""
        if self.tiers:
            top = self.tiers[-1]
            return LadderStep(top.amount, top.tier_text_key)
        return self.steps[0]

    @property
    def floor(self) -> LadderStep:
        if self.tiers:
            low = self.tiers[0]
            return LadderStep(low.amount, low.tier_text_key)
        return self.steps[-1]

    def tier(self, tier_id: str) -> Tier | None:
        """Ступень по идентификатору. `None` — не «пустая ступень», а «такой
        нет»: подставлять вместо неё дефолт значит выставить счёт за объём,
        которого лид не выбирал."""
        return next((t for t in self.tiers if t.id == tier_id), None)


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

    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        raise PricingConfigError(f"позиция {pid!r}: пустой title")

    if raw.get("tiers") is not None:
        return _load_tiered_position(pid, raw, ccy, title, knowledge)

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

    return Position(pid, title, ccy, low, high, tuple(steps),
                    _aliases(pid, raw.get("aliases")))


def _load_tiered_position(pid: str, raw: dict, ccy: str, title: str,
                          knowledge: str) -> Position:
    """Позиция с ПУБЛИЧНЫМИ ступенями объёма.

    Сетка торга здесь запрещена, а не «не используется»: два списка цен на одну
    позицию расходятся не сразу, а через месяц, и наружу это выходит ценой.
    Границы позиции выводятся из ступеней по той же причине — заданные отдельно,
    они были бы вторым источником."""
    for forbidden in ("ladder", "price_range"):
        if raw.get(forbidden) is not None:
            raise PricingConfigError(
                f"позиция {pid!r}: {forbidden} рядом с tiers запрещён. Ступень "
                f"объёма — одна цена, торга внутри неё нет; два списка на одной "
                f"позиции это второй источник цены")

    raw_tiers = raw.get("tiers")
    if not isinstance(raw_tiers, (list, tuple)) or len(raw_tiers) < 2:
        raise PricingConfigError(
            f"позиция {pid!r}: нужно минимум ДВЕ ступени — одна ступень это не "
            f"выбор объёма, а обычная позиция")

    tiers: list[Tier] = []
    seen_ids: set[str] = set()
    seen_keys: set[str] = set()
    prev: int | None = None
    for i, t in enumerate(raw_tiers):
        if not isinstance(t, dict):
            raise PricingConfigError(f"позиция {pid!r}, ступень {i}: ожидался словарь")
        tid = t.get("id")
        if not isinstance(tid, str) or not tid.strip():
            raise PricingConfigError(f"позиция {pid!r}, ступень {i}: пустой id")
        tid = tid.strip()
        if tid in seen_ids:
            raise PricingConfigError(
                f"позиция {pid!r}, ступень {i}: id {tid!r} повторяется — счёт "
                f"сослался бы на неоднозначную ступень")
        key = t.get("tier_text_key")
        if not isinstance(key, str) or not key.strip():
            raise PricingConfigError(
                f"позиция {pid!r}, ступень {tid!r}: пустой tier_text_key — цена "
                f"без названного объёма это цена ни за что")
        key = key.strip()
        if key in seen_keys:
            raise PricingConfigError(
                f"позиция {pid!r}, ступень {tid!r}: tier_text_key {key!r} "
                f"повторяется — выбор, в котором нечего выбирать")
        amount = _money(t.get("amount"), ccy, f"позиция {pid!r}, ступень {tid!r}")
        if prev is not None and amount.minor <= prev:
            raise PricingConfigError(
                f"позиция {pid!r}, ступень {tid!r}: ступени обязаны строго "
                f"возрастать — это публичный порядок перечисления лиду")
        # Правило №5 читается по КАЖДОЙ ступени: публичны они все, промежуточных
        # среди них не бывает.
        if not _literal_in_knowledge(amount, ccy, knowledge):
            raise PricingConfigError(
                f"позиция {pid!r}, ступень {tid!r}: цена отсутствует в knowledge "
                f"как литерал — бот назвал бы цену, которой клиент не публиковал")
        seen_ids.add(tid)
        seen_keys.add(key)
        prev = amount.minor
        tiers.append(Tier(tid, amount, key,
                          _aliases(f"{pid}:{tid}", t.get("aliases"))))

    return Position(pid, title, ccy, tiers[0].amount, tiers[-1].amount, (),
                    _aliases(pid, raw.get("aliases")), tuple(tiers))


def _aliases(pid: str, raw) -> tuple[str, ...]:
    """Основы слов клиента для узнавания позиции. Регистр снимается здесь, один
    раз: приводить его на каждом ходу разговора — это тихий шанс когда-нибудь
    забыть."""
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise PricingConfigError(f"позиция {pid!r}: aliases обязан быть списком")
    out: list[str] = []
    for a in raw:
        if not isinstance(a, str) or not a.strip():
            raise PricingConfigError(
                f"позиция {pid!r}: пустой алиас — совпал бы с любым словом")
        alias = a.strip().casefold()
        if len(alias.split()) > 1:
            # Предпасс сравнивает алиас с ОДНИМ словом лида. Многословный алиас
            # не совпадёт никогда, но в конфиге выглядит рабочим — это молчаливо
            # неузнаваемая позиция, а не мелочь.
            raise PricingConfigError(
                f"позиция {pid!r}: алиас {a!r} состоит из нескольких слов — "
                f"он не совпадёт ни разу; нужна одна основа слова")
        if alias not in out:
            out.append(alias)
    return tuple(out)


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
