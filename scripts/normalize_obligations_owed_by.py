"""Retro-миграция owed_by в contact_obligations.

Инвариант «owed_by для brief/examples/recalc = bot» введён 2026-07-24
(chatter/core/obligations_slot.filter_model_updates) для НОВЫХ записей. Строки,
записанные ДО фикса с owed_by=client, не рендерятся в brain (render_slot_block —
только owed_by=bot) → открытое обязательство не доезжает до модели. Этот скрипт
чинит их одним UPDATE. Идемпотентен (повтор → 0 изменений).

Запуск (прод): python scripts/normalize_obligations_owed_by.py --db .secrets/demo.db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# Запуск как `python scripts/foo.py` ставит sys.path[0]=scripts/, корень репо не
# на пути → импорт chatter падает. Добавляем корень явно (единый источник видов —
# obligations_slot, дублировать список kinds в скрипте нельзя).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatter.core.obligations_slot import CODE_BOT_OWNED_KINDS  # noqa: E402


def normalize_owed_by(db_path: str) -> int:
    """UPDATE owed_by='bot' для CODE_BOT_OWNED_KINDS, где сейчас не bot. Возвращает
    число исправленных строк (rowcount). Единый источник видов — obligations_slot,
    чтобы миграция и запись не разошлись."""
    kinds = tuple(sorted(CODE_BOT_OWNED_KINDS))
    placeholders = ",".join("?" for _ in kinds)
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            f"UPDATE contact_obligations SET owed_by='bot' "
            f"WHERE kind IN ({placeholders}) AND owed_by != 'bot'", kinds)
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, help="путь к SQLite БД клиента")
    args = ap.parse_args()
    n = normalize_owed_by(args.db)
    print(f"normalized owed_by=bot for {n} row(s) in {args.db}")


if __name__ == "__main__":
    main()
