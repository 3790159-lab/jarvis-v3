"""Миграция схемы 3A. Ключевой тест идёт по КОПИИ ЖИВОЙ базы, а не по свежей:
свежая база проходит миграцию всегда — она пустая, в ней нечему сломаться.
Класс «ломается только на реальных данных» этот проект уже ловил дважды
(cp1251 и cross-thread SQLite)."""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from chatter.storage.db import Store

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIVE_DB = REPO_ROOT / ".secrets" / "chatter_telethon.db"

NEW_CONTACT_COLUMNS = {
    "paused_at", "pause_source", "pause_msg_id",
    "pause_detail", "pause_until", "last_human_out_ts",
}


def _columns(db_path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def test_migrates_a_copy_of_the_live_db_twice_without_losing_data():
    # SKIP громко и с причиной: молчаливый зелёный превратил бы этот тест
    # в пустышку ровно тогда, когда он единственный проверяет реальные данные.
    if not LIVE_DB.exists():
        pytest.skip(f"живой базы нет по пути {LIVE_DB} — тест на реальных данных пропущен")

    tmp = Path(__file__).resolve().parent / "_live_copy.db"
    shutil.copy2(LIVE_DB, tmp)
    try:
        before = sqlite3.connect(str(tmp))
        msgs_before = before.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        contacts_before = before.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        before.close()
        assert msgs_before > 0, "живая база пуста — тест бессмыслен"

        Store(tmp).close()          # первый прогон: миграция
        Store(tmp).close()          # второй прогон: ОБЯЗАН быть no-op, не падать

        assert NEW_CONTACT_COLUMNS <= _columns(tmp, "contacts")
        after = sqlite3.connect(str(tmp))
        assert after.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == msgs_before
        assert after.execute("SELECT COUNT(*) FROM contacts").fetchone()[0] == contacts_before
        after.close()

        backups = list(tmp.parent.glob(f"{tmp.name}.pre-3a-*.bak"))
        assert len(backups) == 1, f"ожидался ровно один бэкап, получено: {backups}"
    finally:
        for p in list(tmp.parent.glob(f"{tmp.name}*")):
            p.unlink(missing_ok=True)


def test_fresh_db_gets_full_schema_and_no_backup(tmp_path):
    db = tmp_path / "fresh.db"
    Store(db).close()
    assert NEW_CONTACT_COLUMNS <= _columns(db, "contacts")
    for table in ("runtime_flags", "console_cards", "control_events"):
        assert _columns(db, table), f"таблица {table} не создана"
    # Бэкапить нечего: базы не было. Мусор в каталоге клиента не плодим.
    assert list(tmp_path.glob("*.bak")) == []
