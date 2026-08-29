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
  · таблица с `contact_id`, не названная ни в одной из ЧЕТЫРЁХ категорий, —
    ОТКАЗ (код 3), и план тоже отказывает. Добавлено 27.08: заказ был про одну
    забытую таблицу, замер схемы показал ТРИ (`outgoing_queue`,
    `funnel_transitions`, `status_index`). Одна забытая строка — случайность,
    три — отсутствие правила, поэтому чинится не список, а его непокрытость;
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
DRILL_CONTACTS = frozenset({"telegram:237616472:volska",
                            "telegram:8849893367:volska",
                            "telegram:8849893367:yarina"})

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
                "quotes", "invoices", "payments",
                # 27.08. Задание, поставленное ДО сброса, к сценарию прогона
                # отношения не имеет: сброс объявляет, что диалога не было, —
                # значит и обещания написать в него нет.
                #
                # 🔴 ИЗ ТРЁХ СИРОТ ЭТА — САМАЯ ОПАСНАЯ, и не потому, что новее.
                # У неё есть ИСПОЛНИТЕЛЬ: раннер смотрит в очередь каждые пять
                # секунд (`OUTGOING_POLL_INTERVAL_SECONDS = 5.0`). Две другие
                # давали лишние строки в отчётах, эта даёт ДЕЙСТВИЕ: сброс
                # отчитался о чистоте, раннер забрал pending-строку, отправил
                # её лиду — и тот же `begin_takeover` заглушил бота ПОСРЕДИ
                # прогона. Забытый `paused=1` даёт молчание С НАЧАЛА и виден
                # сразу; это — молчание в середине, неотличимое от «сброс
                # сработал, а потом пришла отправка».
                "outgoing_queue")

# Ступени счёта: своего `contact_id` у них НЕТ, они принадлежат счёту. Чистятся
# подзапросом и СТРОГО ДО `invoices` — иначе счёт удалён, а ступени осиротели.
_WIPE_BY_INVOICE = ("invoice_stages",)

_BY_INVOICE_WHERE = ("WHERE invoice_id IN (SELECT invoice_id FROM invoices"
                     " WHERE contact_id=?)")

# Что переживает сброс НАМЕРЕННО (см. контракт в docstring).
#
# Две записи добавлены 27.08 решением владельца, и обе — про НАМЕРЕНИЕ, а не
# про текущее поведение. Без этого списка «оставили» через полгода неотличимо
# от «забыли»: обе таблицы и до сегодня не чистились, только молча.
_KEEP_TABLES = ("llm_usage", "control_events",
                # ЛЕНТА ПЕРЕХОДОВ — тот же класс, что `control_events`: запись
                # о том, что происходило, а не состояние, из которого бот
                # делает следующий шаг. Стирать её значило бы переписывать
                # бухгалтерию, а контракт сброса это прямо запрещает.
                # ⚠️ ОГОВОРКА ВСЛУХ (решение владельца 27.08): она КОПИТСЯ
                # сквозь сбросы, мы это знаем и оставляем осознанно.
                "funnel_transitions",
                # ПРОИЗВОДНАЯ таблица: `issue_status_index` перевыпускает её
                # целиком, начиная с `DELETE FROM status_index`. Чистить
                # производное здесь значило бы завести ВТОРОЕ место, где оно
                # чистится, — и однажды они разойдутся.
                "status_index")

# ЧЕТВЁРТАЯ КАТЕГОРИЯ (27.08). Строка не удаляется, а приводится к исходному
# состоянию — механизм в `_CONTACT_RESET_SQL` ниже.
#
# 🔴 ЗАЧЕМ ОТДЕЛЬНОЙ КАТЕГОРИЕЙ, А НЕ ИСКЛЮЧЕНИЕМ. `contacts` покрыта ТРЕТЬИМ
# способом, и проверка «названа ли таблица в одном из двух списков» краснела бы
# на ней ЛОЖНО. Первый же автор сторожа добавил бы её в исключения — а
# исключение, добавленное чтобы сторож замолчал, это дыра с подписью.
# Категорий обязано быть столько, сколько РЕАЛЬНЫХ способов обращения.
_RESET_IN_PLACE = ("contacts",)

# Все четыре способа — ОДНИМ местом, и это единственный источник правды о
# покрытии. Списки остаются литеральными и правятся руками: выведенный из
# схемы список согласен со схемой по определению и промолчит там, где человек
# не подумал.
_ALL_CATEGORIES = {
    "_WIPE_TABLES": _WIPE_TABLES,
    "_WIPE_BY_INVOICE": _WIPE_BY_INVOICE,
    "_KEEP_TABLES": _KEEP_TABLES,
    "_RESET_IN_PLACE": _RESET_IN_PLACE,
}

# Код возврата отказа по непокрытой таблице — СВОЙ, не 1 и не 2: «база уехала
# вперёд кода» и «не тот контакт» чинятся по-разному, и один код на две беды
# отправил бы человека не туда.
RC_UNCOVERED_TABLE = 3

# Стадия воронки + всё, что глушит бота. Забытый paused=1 означает, что бот
# молчит, и весь прогон честно упирается в таймаут — «сброшенный» контакт
# обязан быть говорящим.
_CONTACT_RESET_SQL = (
    "UPDATE contacts SET state='new', paused=0,"
    " paused_at=NULL, pause_source=NULL, pause_msg_id=NULL, pause_detail=NULL,"
    " pause_until=NULL, last_human_out_ts=NULL WHERE contact_id=?")


def tables_with_contact_id(conn) -> list[str]:
    """Таблицы ЖИВОЙ схемы, у которых есть колонка `contact_id`.

    Читается из самой базы, а не из литерала: список, выведенный из кода,
    согласится с кодом по определению и промолчит ровно на той таблице, которую
    забыли, — то есть повторит чинимый дефект.
    """
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
        " AND name NOT LIKE 'sqlite_%'")]
    out = []
    for t in sorted(names):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
        if "contact_id" in cols:
            out.append(t)
    return out


def coverage_problems(conn) -> list[str]:
    """Пусто — покрытие полное. Иначе список бед ЧЕЛОВЕЧЕСКИМИ фразами.

    Две беды, и они разные:

      * таблица с `contact_id` не названа НИ В ОДНОЙ категории — база уехала
        вперёд кода, и сброс не знает, что с ней делать;
      * таблица названа в ДВУХ — два ответа на один вопрос, и какой из них
        исполнится, решает порядок в коде.

    Текст отказа обязан называть таблицу И обе возможности словами. Отказ, не
    говорящий, что делать, превращается в «просто добавь куда-нибудь».
    """
    problems: list[str] = []

    seen: dict[str, list[str]] = {}
    for cat, tables in _ALL_CATEGORIES.items():
        for t in tables:
            seen.setdefault(t, []).append(cat)
    for t, cats in sorted(seen.items()):
        if len(cats) > 1:
            problems.append(
                f"таблица `{t}` названа СРАЗУ В ДВУХ категориях"
                f" ({', '.join(cats)}) — два ответа на один вопрос,"
                f" и какой исполнится, решает порядок в коде. Оставь одну.")

    for t in tables_with_contact_id(conn):
        if t not in seen:
            problems.append(
                f"таблица `{t}` не названа ни в одной категории. Добавь её в"
                f" `_WIPE_TABLES` (стирать вместе с перепиской) или в"
                f" `_KEEP_TABLES` (не трогать намеренно) — и напиши, ПОЧЕМУ."
                f" Пока она не названа, сброс отчитается о чистоте, которой"
                f" нет.")
    return problems


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
        "SELECT state, paused FROM contacts WHERE contact_id=?",
        (contact,)).fetchone()
    out["_contact"] = row
    return out


def _print_counts(title: str, c: dict) -> None:
    print(f"— {title} —")
    row = c["_contact"]
    if row is None:
        print("  контакта нет в таблице contacts")
    else:
        state, paused = row
        print(f"  воронка: state={state}, paused={paused}")
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
        # ПОКРЫТИЕ — ПЕРВЫМ ДЕЛОМ, до любой записи и до печати плана.
        #
        # 🔴 ОТКАЗ, А НЕ ПРЕДУПРЕЖДЕНИЕ. Решение владельца 27.08, три довода:
        #   1. предупреждение в дриле НИКТО НЕ ПРОЧТЁТ — оно уедет в лог рядом
        #      с десятками штатных строк и будет замечено после того, как
        #      прогон соврал;
        #   2. цена ошибки несимметрична: ложный отказ стоит одной строки в
        #      списке и двух минут, пропущенная таблица — прогона, который
        #      СНАЧАЛА ВЫГЛЯДЕЛ исправным;
        #   3. отказ приходит В ДЕНЬ появления таблицы, а не на прогоне через
        #      месяц.
        #
        # И ПЛАН ТОЖЕ ОТКАЗЫВАЕТ (§6.8): сухой прогон, показавший чистоту,
        # которой не будет, — это то же враньё, только на шаг раньше.
        problems = coverage_problems(conn)
        if problems:
            print(f"ОТКАЗ: схема базы {a.db} не покрыта списками сброса.")
            for p in problems:
                print(f"  · {p}")
            print("\nНи одна строка не изменена.")
            return RC_UNCOVERED_TABLE

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
