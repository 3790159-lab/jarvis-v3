"""Снять руками фантомные обязательства из слота (P17).

Повод: живой дрил 2026-07-25 нашёл у volska два открытых обязательства с
`owed_by=bot`, которые по смыслу — ход КЛИЕНТА («клієнт ще не оплатив», «клієнт
ще не обрав спосіб оплати»). Пока они открыты, они рендерятся в промпт brain
как «відпрацювати ПЕРЕД хендоффом» и бот дожимает лида по тому, что тот должен
сделать сам. Код-фикс отдельно (спека P17); этот скрипт — уборка последствий.

Контракт (потому что пишем в ЖИВУЮ клиентскую БД):
  · без `--apply` не меняется НИЧЕГО — печатается только план;
  · снимаем `cancelled`, а не DELETE: строка остаётся в истории, вернуть можно;
  · повтор — no-op: уже снятое не переписывается (момент закрытия достоверен);
  · названный ключ, которого нет, — НЕ успех (код 1), а не тихий ноль;
  · состояние печатается ДО и ПОСЛЕ целиком, чтобы правку можно было сверить.

    python scripts/drop_phantom_obligations.py .secrets/demo.db \
        --contact 237616472:volska --okey "other:клієнт ще не оплатив, ці" --apply
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time

# Жёсткое удаление (--delete) разрешено ТОЛЬКО на дрил-контакте. На живом
# клиенте это стирание истории его обязательств: строка `cancelled` остаётся
# уликой, а удалённая — нет. Список явный и короткий, менять осознанно.
DRILL_CONTACTS = frozenset({"237616472:volska", "8849893367:volska"})


def snapshot(conn, contact: str) -> list[tuple]:
    return list(conn.execute(
        "SELECT okey, kind, owed_by, status, detail FROM contact_obligations "
        "WHERE contact_id=? ORDER BY okey", (contact,)))


def _print_snapshot(title: str, rows) -> None:
    print(f"— {title} —")
    if not rows:
        print("  (слот пуст)")
    for okey, kind, owed_by, status, detail in rows:
        print(f"  [{status:<9}] {okey}  (kind={kind}, owed_by={owed_by}) — {detail}")


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--contact", required=True)
    ap.add_argument("--okey", action="append", default=[],
                    help="ключ обязательства; можно повторять")
    ap.add_argument("--apply", action="store_true",
                    help="применить (без флага печатается только план)")
    ap.add_argument("--delete", action="store_true",
                    help="УДАЛИТЬ строку целиком, а не снять в cancelled: дрилу "
                         "нужен свободный ключ, иначе классификатор переиспользует "
                         "старую строку вместо создания новой (прогон №5)")
    a = ap.parse_args(argv)

    if not a.okey:
        print("нужен хотя бы один --okey")
        return 2
    if a.delete and a.contact not in DRILL_CONTACTS:
        print(f"ОТКАЗ: --delete разрешён только на дрил-контакте "
              f"({', '.join(sorted(DRILL_CONTACTS))}), а не на {a.contact}. "
              f"Для живого клиента используй снятие в cancelled.")
        return 2

    conn = sqlite3.connect(a.db, timeout=10.0)
    try:
        before = snapshot(conn, a.contact)
        _print_snapshot("ДО", before)

        have = {r[0]: r[3] for r in before}
        missing = [k for k in a.okey if k not in have]
        # В режиме удаления берём строку в ЛЮБОМ статусе: занятый ключ мешает
        # дрилу независимо от того, open он или cancelled.
        todo = [k for k in a.okey
                if (k in have) if a.delete or have[k] == "open"]
        already = [] if a.delete else [
            k for k in a.okey if k in have and have[k] != "open"]

        print()
        for k in already:
            print(f"  ⏭ уже снято ранее ({have[k]}): {k}")
        for k in missing:
            # Отсутствие ключа при удалении — норма (повтор), при снятии —
            # опечатка или не та БД, и молчать об этом нельзя (DEV-18).
            print(f"  {'⏭ уже удалено' if a.delete else '🔴 не найдено у контакта'}: {k}")
        for k in todo:
            print(f"  {'🗑 УДАЛЯЮ строку целиком' if a.delete else '✂️ снимаю (open → cancelled)'}: {k}")

        if not a.apply:
            print("\nэто ПЛАН. Применить: добавь --apply")
            return 0 if (a.delete or not missing) else 1

        if todo and a.delete:
            conn.executemany(
                "DELETE FROM contact_obligations WHERE contact_id=? AND okey=?",
                [(a.contact, k) for k in todo])
            conn.commit()
        elif todo:
            now = time.time()
            conn.executemany(
                "UPDATE contact_obligations SET status='cancelled', closed_ts=? "
                "WHERE contact_id=? AND okey=? AND status='open'",
                [(now, a.contact, k) for k in todo])
            conn.commit()

        print()
        _print_snapshot("ПОСЛЕ", snapshot(conn, a.contact))
        return 0 if (a.delete or not missing) else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
