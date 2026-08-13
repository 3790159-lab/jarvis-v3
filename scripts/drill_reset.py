"""Сброс состояния дрил-контакта перед прогоном (стенд v2, Э4).

Повод: без сброса слот тащит состояние прошлых прогонов, и проверки зеленеют
ещё до старта — в прогоне 25.07 три из четырёх obligations-проверок были
зелены на первом же шаге. Регресс требует предсказуемого старта, иначе он
проверяет не сценарий, а осадок.

Контракт (пишем в БД, где лежит ЖИВАЯ переписка клиента):
  · контакт обязан быть в `DRILL_CONTACTS` — иначе ОТКАЗ (код 2) ещё до
    открытия БД. Это стирание истории, а не правка флага: `cancelled` можно
    вернуть, удалённую переписку — нет;
  · без `--apply` не меняется НИЧЕГО, печатается только план;
  · чужие контакты не задеваются (всё по contact_id);
  · `llm_usage` (расходы на LLM) и `control_events` (улики действий владельца)
    переживают сброс: подготовка сценария не переписывает бухгалтерию;
  · а вот СОСТОЯНИЕ сделки (`quotes`, `invoices`, `invoice_stages`, `payments`)
    стирается вместе с перепиской — это тот же осадок, а не бухгалтерия.
    Прогон 12.08: диалог сброшен, активная котировка осталась, и следующий
    «готовий замовити» дал бы счёт за работу, которой в этом диалоге никто не
    упоминал (`_with_quote_fallback` берёт позицию из активной котировки);
  · контакта нет в БД — это код 1, а не тихий ноль (опечатка в id или не та
    БД, DEV-18);
  · повтор — no-op с кодом 0.

    python scripts/drill_reset.py .secrets/demo.db --contact 237616472:volska --apply
"""
from __future__ import annotations

import argparse
import sqlite3
import sys

# Сброс разрешён ТОЛЬКО на дрил-контакте. Список ДУБЛИРУЕТ такой же в
# drop_phantom_obligations.py, и тест-сторож (tests/test_drill_reset.py)
# требует их совпадения: два разошедшихся списка — это способ однажды
# стереть переписку живого клиента. Новый тестовый аккаунт добавляется В ОБА.
# Суффикс — ПЕРСОНА, а не аккаунт: тот же тестовый аккаунт под демо-составом
# yarina даёт другой contact_id, и без него авто-лид отказывается работать
# (`drill_runner.py:507`). Добавлено 14.08 под демо третьего клиента.
DRILL_CONTACTS = frozenset({"237616472:volska", "8849893367:volska",
                            "8849893367:yarina"})

# Что стирается. `facts` и `contact_profile` — то, из-за чего `profile_contains`
# зеленеет за счёт прошлых прогонов; `console_cards` — карточки, чьи msg_id
# после сброса ведут в никуда.
_WIPE_TABLES = ("messages", "contact_profile", "contact_obligations",
                "facts", "console_cards",
                # Деньги дрил-контакта — тоже осадок. Прогон 12.08: сброшенный
                # диалог, но пережившая сброс АКТИВНАЯ котировка, а
                # `_with_quote_fallback` берёт позицию именно из неё, когда лид
                # услугу не назвал. Следующий «готовий замовити» дал бы счёт за
                # работу, которой в этом диалоге никто не упоминал.
                "quotes", "invoices", "payments")

# Ступени счёта: своего `contact_id` у них НЕТ, они принадлежат счёту. Чистятся
# подзапросом и СТРОГО ДО `invoices` — иначе счёт удалён, а ступени осиротели.
_WIPE_BY_INVOICE = ("invoice_stages",)

_BY_INVOICE_WHERE = ("WHERE invoice_id IN (SELECT invoice_id FROM invoices"
                     " WHERE contact_id=?)")

# Что переживает сброс намеренно (см. контракт в docstring).
_KEEP_TABLES = ("llm_usage", "control_events")

# Стадия воронки + всё, что глушит бота. Забытый paused=1 означает, что бот
# молчит, и весь прогон честно упирается в таймаут — «сброшенный» контакт
# обязан быть говорящим.
_CONTACT_RESET_SQL = (
    "UPDATE contacts SET state='new', paused=0, human_took_over=0,"
    " paused_at=NULL, pause_source=NULL, pause_msg_id=NULL, pause_detail=NULL,"
    " pause_until=NULL, last_human_out_ts=NULL WHERE contact_id=?")


def counts(conn, contact: str) -> dict:
    """Сколько строк у контакта в каждой затрагиваемой таблице + строка воронки."""
    out: dict = {}
    for table in _WIPE_TABLES:
        out[table] = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE contact_id=?", (contact,)).fetchone()[0]
    for table in _WIPE_BY_INVOICE:
        out[table] = conn.execute(
            f"SELECT COUNT(*) FROM {table} {_BY_INVOICE_WHERE}",
            (contact,)).fetchone()[0]
    row = conn.execute(
        "SELECT state, paused, human_took_over FROM contacts WHERE contact_id=?",
        (contact,)).fetchone()
    out["_contact"] = row
    return out


def _print_counts(title: str, c: dict) -> None:
    print(f"— {title} —")
    row = c["_contact"]
    if row is None:
        print("  контакта нет в таблице contacts")
    else:
        state, paused, took_over = row
        print(f"  воронка: state={state}, paused={paused}, human_took_over={took_over}")
    for table in (*_WIPE_TABLES, *_WIPE_BY_INVOICE):
        print(f"  {table}: {c[table]}")


def reset(conn, contact: str) -> None:
    # Сначала то, что опознаётся ЧЕРЕЗ счёт, и только потом сами счета.
    for table in _WIPE_BY_INVOICE:
        conn.execute(f"DELETE FROM {table} {_BY_INVOICE_WHERE}", (contact,))
    for table in _WIPE_TABLES:
        conn.execute(f"DELETE FROM {table} WHERE contact_id=?", (contact,))
    conn.execute(_CONTACT_RESET_SQL, (contact,))
    conn.commit()


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    ap = argparse.ArgumentParser(description="Сброс состояния дрил-контакта.")
    ap.add_argument("db")
    ap.add_argument("--contact", required=True,
                    help="contact_id вида <peer>:<persona>, только из DRILL_CONTACTS")
    ap.add_argument("--apply", action="store_true",
                    help="применить (без флага печатается только план)")
    a = ap.parse_args(argv)

    # Предохранитель ДО открытия БД: отказ одинаков и для плана, и для --apply,
    # чтобы «просто посмотреть на клиентском контакте» не стало привычкой.
    if a.contact not in DRILL_CONTACTS:
        print(f"ОТКАЗ: сброс разрешён только на дрил-контакте "
              f"({', '.join(sorted(DRILL_CONTACTS))}), а не на {a.contact}. "
              f"Это стирание истории живой переписки.")
        return 2

    conn = sqlite3.connect(a.db, timeout=10.0)
    try:
        before = counts(conn, a.contact)
        _print_counts("ДО", before)
        if before["_contact"] is None:
            print(f"\n🔴 контакт {a.contact} не найден в БД {a.db} — "
                  f"опечатка в id или не та БД, сброс не выполнен")
            return 1

        print(f"\nсбрасываю: {', '.join((*_WIPE_TABLES, *_WIPE_BY_INVOICE))}"
              f" + стадия воронки")
        print(f"не трогаю: {', '.join(_KEEP_TABLES)}")
        if not a.apply:
            print("\nэто ПЛАН. Применить: добавь --apply")
            return 0

        reset(conn, a.contact)
        print()
        _print_counts("ПОСЛЕ", counts(conn, a.contact))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
