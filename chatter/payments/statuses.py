"""Статусы счёта: закрытое перечисление + карта переходов ДАННЫМИ (спека §3.2,
§14 пп.4-5).

Ф0 производит четыре статуса из десяти. Перечисление и карта вводятся целиком
осознанно: в Ф1 появляются `partially_paid`/`overpaid`, и любое написанное
сегодня `status == "paid"` начнёт молча врать. Поэтому наружу торчит
`is_settled()`, а не строка.

Идиома «карта переходов — данные» взята у воронки (`conversation.py:31`).

Актора «клиент» здесь нет вообще — это техническая форма жёсткого правила №2
(«я оплатил» словами не событие): ветку «клиент подтвердил» некуда написать.
"""
from __future__ import annotations

INVOICE_STATUSES: tuple[str, ...] = (
    "draft",             # создан, ещё ничего не решено
    "awaiting_owner",    # сложный запрос (§2.4) либо backstop по сумме (§5.3)
    "issued",            # ушёл клиенту
    "partially_paid",    # Ф1
    "paid",
    "overpaid",          # Ф1
    "overdue",           # пометка, НЕ отмена
    "cancelled",
    "refund_requested",  # Ф3
    "refunded",          # Ф3
)

# `upsell` — УЗКИЙ актор, а не «система вообще». Снять выставленный счёт это
# денежное решение, и правом на него не должен обладать любой код бота:
# добавь сюда `system` — и завтра счёт снимет любая ветка. Этот актор
# рождается ровно в одном месте (замена счёта на более дорогой при нулевых
# поступлениях), и его предусловия проверены ТАМ же.
ACTORS: tuple[str, ...] = ("owner", "provider", "system", "upsell")

_SYSTEM = frozenset({"system"})
_OWNER = frozenset({"owner"})
_UPSELL = frozenset({"upsell"})

# (откуда, куда, кто вправе). Денежные переходы — ВСЕГДА `system`: статус это
# проекция от строк оплаты (§14 п.4), а не то, что кто-то ставит рукой. Владелец
# ТАПАЕТ «Оплачено» → пишется payment(confirmed_by=owner) → пересчёт двигает
# статус. Если бы владелец мог поставить `paid` напрямую, в Ф1 он закрыл бы
# счёт, у которого оплачена половина.
TRANSITIONS: tuple[tuple[str, str, frozenset[str]], ...] = (
    ("draft", "issued", _SYSTEM),
    ("draft", "awaiting_owner", _SYSTEM),
    ("draft", "cancelled", _OWNER),
    ("awaiting_owner", "issued", _OWNER),
    ("awaiting_owner", "cancelled", _OWNER),

    ("issued", "partially_paid", _SYSTEM),
    ("issued", "paid", _SYSTEM),
    ("issued", "overpaid", _SYSTEM),
    ("issued", "overdue", _SYSTEM),
    ("issued", "cancelled", _OWNER | _UPSELL),

    # Просрочка — пометка, а не конец: просроченный счёт остаётся оплачиваемым,
    # и автоотмены по сроку нет (§3.2). Отменить может только владелец.
    ("overdue", "partially_paid", _SYSTEM),
    ("overdue", "paid", _SYSTEM),
    ("overdue", "overpaid", _SYSTEM),
    ("overdue", "cancelled", _OWNER),

    ("partially_paid", "paid", _SYSTEM),
    ("partially_paid", "overpaid", _SYSTEM),
    ("partially_paid", "overdue", _SYSTEM),
    ("partially_paid", "cancelled", _OWNER),

    # Переплату разносит ТОЛЬКО владелец: зачесть или вернуть — движение денег.
    ("overpaid", "paid", _OWNER),
    ("overpaid", "refund_requested", _OWNER),

    # Заявку на возврат принимает бот, деньги возвращает владелец (§3.4).
    ("paid", "refund_requested", _SYSTEM),
    ("refund_requested", "refunded", _OWNER),
    ("refund_requested", "paid", _OWNER),      # заявку отклонили
)

# Деньги закрыты только здесь. `partially_paid` недоплачен, `overpaid` ждёт
# решения владельца — ни один из них не «оплачено».
_SETTLED = frozenset({"paid"})


class TransitionError(ValueError):
    """Недопустимый переход, неизвестный статус или неизвестный актор."""


_MAP: dict[tuple[str, str], frozenset[str]] = {
    (frm, to): actors for frm, to, actors in TRANSITIONS
}


def allowed_actors(frm: str, to: str) -> frozenset[str]:
    """Кто вправе провести переход. Пустое множество = ребра нет."""
    return _MAP.get((frm, to), frozenset())


def assert_transition(frm: str, to: str, actor: str) -> None:
    """Бросает `TransitionError`, если переход недопустим. Молчаливый отказ
    запрещён (DEV-18): «ничего не произошло» неотличимо от «сделано»."""
    if frm not in INVOICE_STATUSES:
        raise TransitionError(f"неизвестный статус {frm!r}")
    if to not in INVOICE_STATUSES:
        raise TransitionError(f"неизвестный статус {to!r}")
    if actor not in ACTORS:
        raise TransitionError(
            f"неизвестный актор {actor!r}: допустимы {ACTORS}. Клиента среди "
            f"акторов нет — «я оплатил» словами не событие (правило №2)")
    actors = allowed_actors(frm, to)
    if not actors:
        raise TransitionError(f"перехода {frm} → {to} не существует")
    if actor not in actors:
        raise TransitionError(
            f"{frm} → {to} доступен только для {sorted(actors)}, не для {actor!r}")


def is_settled(status: str) -> bool:
    """Единственный способ спросить «деньги закрыты?». Прямое сравнение со
    строкой `"paid"` запрещено контрактом §14 п.5."""
    if status not in INVOICE_STATUSES:
        raise TransitionError(f"неизвестный статус {status!r}")
    return status in _SETTLED
