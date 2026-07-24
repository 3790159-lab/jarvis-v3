"""Retro-чистка дублирующих 'other' обязательств.

Анти-фрагментация введена 2026-07-24 (merge_obligations) для НОВЫХ ходов: пока
открыт канонический долг (brief/examples/recalc/owner_write), новый 'other' не
создаётся. Строки, записанные ДО фикса (дрил Д-10 T2: при открытом brief создан
дублирующий 'other:уточнити'), чистит этот скрипт — удаляет открытые 'other' у
контактов, где открыт канонический долг. Идемпотентен (повтор → 0).

Pure stdlib (как obligations_cost_delta.py) → запускается напрямую:
    python scripts/cleanup_duplicate_other_obligations.py --db .secrets/demo.db
"""
from __future__ import annotations

import argparse
import sqlite3


def cleanup_duplicate_others(db_path: str) -> int:
    """DELETE открытых 'other' для контактов с открытым каноническим долгом.
    Возвращает число удалённых строк."""
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "DELETE FROM contact_obligations "
            "WHERE kind='other' AND status='open' AND contact_id IN ("
            "  SELECT contact_id FROM contact_obligations "
            "  WHERE kind != 'other' AND status='open')")
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, help="путь к SQLite БД клиента")
    args = ap.parse_args()
    n = cleanup_duplicate_others(args.db)
    print(f"removed {n} duplicate 'other' obligation(s) in {args.db}")


if __name__ == "__main__":
    main()
