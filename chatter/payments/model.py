"""Чистая часть модели счёта: ключ идемпотентности и проекция статуса
(спека §3.1-§3.3, §14 пп.2, 4, 5).

Два решения, из-за которых этот модуль существует отдельно от хранилища:

1. **Ключ идемпотентности один, с префиксом источника.** Прежняя схема имела
   `UNIQUE(contact_id, card_msg_id)` и сентинел `0` для вызывателей без
   карточки. Сентинел стабилен ровно до появления ВТОРОГО бескарточного
   источника: сегодня это веб-панель (её оплаты по одному контакту схлопываются
   в одну — цена, признанная в коде), в Ф2 это все webhook'и провайдера.
   `dedup_key` с префиксом снимает класс целиком: личность приходит из самого
   события (урок §7.1), а новый источник добавляет префикс, а не колонку.

2. **Статус — проекция от сумм, а не запись.** `project_status` не трогает БД,
   поэтому проверяется всеми граничными случаями, включая те, которых Ф0 не
   производит. Ф1 их включит, не переписывая ветвление.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from chatter.payments.money import Money
from chatter.payments.statuses import INVOICE_STATUSES, TransitionError, assert_transition

# Источники денежной записи. Закрытый набор: новый источник обязан быть
# объявлен здесь, а не появиться строкой на месте вызова.
DEDUP_SOURCES: tuple[str, ...] = ("tap", "panel", "evt")

# Кто вправе подтвердить деньги. Клиента нет — «я оплатил» словами не событие.
CONFIRMED_BY: tuple[str, ...] = ("owner", "provider")

# Статусы, которых проекция не касается: они не про деньги, а про решение
# человека. Отменённый счёт не воскресает от поступления, черновик не
# становится оплаченным в обход выставления.
_NOT_MONEY = frozenset({"draft", "awaiting_owner", "cancelled",
                        "refund_requested", "refunded"})


class DedupKeyError(ValueError):
    """Ключ идемпотентности отсутствует, пуст или из необъявленного источника."""


def make_dedup_key(source: str, ident) -> str:
    """Собрать ключ вида `tap:12345` / `panel:<token>` / `evt:wise:EV-77`."""
    if source not in DEDUP_SOURCES:
        raise DedupKeyError(
            f"источник {source!r} не объявлен: допустимы {DEDUP_SOURCES}")
    text = "" if ident is None else str(ident).strip()
    if not text:
        raise DedupKeyError(
            f"пустая личность события для источника {source!r} — это сентинел под "
            f"другим именем: две записи схлопнулись бы в одну")
    return f"{source}:{text}"


def validate_dedup_key(key: str | None) -> None:
    if not isinstance(key, str) or not key.strip():
        raise DedupKeyError("пустой dedup_key")
    head, sep, tail = key.partition(":")
    if not sep or head not in DEDUP_SOURCES or not tail.strip():
        raise DedupKeyError(
            f"dedup_key {key!r} обязан быть вида <{'|'.join(DEDUP_SOURCES)}>:<личность>")


@dataclass(frozen=True)
class PaymentRecord:
    """Одна денежная запись. `amount_received` отдельно от `amount` с первого
    дня: в Ф2 остаток считается по ЗАЧИСЛЕННОМУ, иначе комиссия конверсии
    оставит вечную недоплату и счёт не закроется никогда (риск 10.4)."""
    contact_id: str
    dedup_key: str
    ts: float
    confirmed_by: str
    amount: Money | None
    amount_received: Money | None = None
    invoice_id: str | None = None
    stage_no: int | None = None
    channel_id: str | None = None
    external_event_id: str | None = None

    def __post_init__(self) -> None:
        validate_dedup_key(self.dedup_key)
        if self.confirmed_by not in CONFIRMED_BY:
            raise ValueError(
                f"confirmed_by={self.confirmed_by!r}: допустимы {CONFIRMED_BY}. "
                f"Клиента среди подтверждающих нет (правило №2)")
        if not self.contact_id:
            raise ValueError("пустой contact_id")
        if self.amount_received is None:
            # В Ф0 конверсии нет: зачислено == заявлено.
            object.__setattr__(self, "amount_received", self.amount)


def project_status(*, amount_total_minor: int | None, received_minor: int,
                   current: str, due_ts: float | None, now: float) -> str:
    """Статус счёта как ФУНКЦИЯ от сумм. Единственный источник статуса.

    Проверяет собственный вывод по карте переходов: если проекция и карта
    разойдутся, это упадёт, а не запишет статус, которого на карте нет."""
    # Неизвестный статус обязан упасть в любой ветке, включая «ничего не
    # меняем»: опечатка в статусе не должна выглядеть как «всё в порядке».
    if current not in INVOICE_STATUSES:
        raise TransitionError(f"неизвестный статус {current!r}")

    if current in _NOT_MONEY:
        return current

    overdue = due_ts is not None and now > due_ts

    if amount_total_minor is None:
        # Сравнивать не с чем. «Наверное хватило» — выдуманный факт.
        target = "overdue" if overdue else current
    elif received_minor <= 0:
        target = "overdue" if overdue else current
    elif received_minor > amount_total_minor:
        target = "overpaid"
    elif received_minor == amount_total_minor:
        target = "paid"
    else:
        target = "overdue" if overdue else "partially_paid"

    if target != current:
        assert_transition(current, target, "system")
    return target
