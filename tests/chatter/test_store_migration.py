"""Миграция схемы 3A. Ключевой тест идёт по КОПИИ ЖИВОЙ базы, а не по свежей:
свежая база проходит миграцию всегда — она пустая, в ней нечему сломаться.
Класс «ломается только на реальных данных» этот проект уже ловил дважды
(cp1251 и cross-thread SQLite)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from chatter.storage.db import Store, _ADDED_COLUMNS

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIVE_DB = REPO_ROOT / ".secrets" / "chatter_telethon.db"

NEW_CONTACT_COLUMNS = {
    "paused_at", "pause_source", "pause_msg_id",
    "pause_detail", "pause_until", "last_human_out_ts",
}

# Базовые колонки `contacts` арки 1/2 — то, что было ДО этой миграции.
# Используется только тестом-стражем ниже, никогда — продовым кодом.
ARC1_CONTACT_COLUMNS = {"contact_id", "state", "paused", "human_took_over"}


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
    try:
        # sqlite backup API, а не shutil.copy2: прод-раннер пишет в LIVE_DB
        # прямо сейчас, copy2 снимает файл побайтово и может поймать
        # неконсистентный срез посреди записи (или PermissionError) — редкий
        # флаки. Backup API снимает консистентный снапшот через сам sqlite,
        # открыт read-only (mode=ro), так что тест гарантированно не может
        # писать в прод-базу.
        src = sqlite3.connect(f"file:{LIVE_DB.as_posix()}?mode=ro", uri=True)
        dst = sqlite3.connect(str(tmp))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

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


def test_fresh_schema_columns_match_arc1_base_plus_added_columns(tmp_path):
    """Тест-страж против дрейфа между `_SCHEMA` (CREATE TABLE, свежие базы)
    и `_ADDED_COLUMNS` (ALTER, старые базы) — это два места, вручную
    объявляющие одни и те же колонки `contacts`. Если кто-то добавит 7-ю
    колонку в `_SCHEMA` и забудет продублировать в `_ADDED_COLUMNS`,
    свежая база её получит (этот тест поймает несовпадение множеств СРАЗУ),
    а уже смигрированная прод-база — никогда: её `_missing_columns()`
    будет пуст навсегда (там сравнение идёт только с `_ADDED_COLUMNS`),
    и она упадёт `no such column` в проде, тихо, при первом обращении.
    Это тест-страж, фиксирующий инвариант, а не тест бага — на текущем
    коде множества сходятся и он должен быть зелёным.
    """
    db = tmp_path / "drift_guard.db"
    Store(db).close()
    fresh_columns = _columns(db, "contacts")
    expected = ARC1_CONTACT_COLUMNS | set(_ADDED_COLUMNS["contacts"])
    assert fresh_columns == expected, (
        f"колонки contacts из _SCHEMA разошлись с ARC1_CONTACT_COLUMNS | _ADDED_COLUMNS: "
        f"лишние в _SCHEMA={fresh_columns - expected}, "
        f"отсутствуют в _SCHEMA={expected - fresh_columns}"
    )


def test_migration_from_explicit_old_schema_preserves_cyrillic_data(tmp_path):
    """Фикстура старой (арка 1/2) схемы прописана ЯВНЫМ SQL прямо здесь, а
    НЕ импортирована из db.py — если импортировать `_SCHEMA`, тест начнёт
    проверять сам себя и не заметит, что кто-то сломал миграцию именно СО
    старой прод-схемы. Гоняется на КАЖДОМ клоне (не только там, где есть
    .secrets/chatter_telethon.db), так что требование «миграция не теряет
    реальные данные» перестаёт вырождаться в пустышку из-за skip в CI.
    Кириллица в текстах — этот проект уже дважды ловил cp1251, молча
    коверкавший текст на Windows.
    """
    db_path = tmp_path / "old_schema.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE contacts (
            contact_id TEXT PRIMARY KEY,
            state TEXT NOT NULL DEFAULT 'new',
            paused INTEGER NOT NULL DEFAULT 0,
            human_took_over INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id TEXT NOT NULL,
            role TEXT NOT NULL,
            text TEXT NOT NULL,
            ts REAL NOT NULL
        );
        CREATE TABLE facts (
            contact_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (contact_id, key)
        );
    """)
    conn.execute(
        "INSERT INTO contacts(contact_id, state, paused, human_took_over) VALUES (?,?,?,?)",
        ("111222333", "active", 0, 0),
    )
    texts = [
        "Привет! Как дела?",
        "Всё хорошо, спасибо большое — а у тебя как?",
        "Договорились, до встречи в среду \U0001F642",
    ]
    for i, text in enumerate(texts):
        conn.execute(
            "INSERT INTO messages(contact_id, role, text, ts) VALUES (?,?,?,?)",
            ("111222333", "user" if i % 2 == 0 else "assistant", text, 1000.0 + i),
        )
    conn.commit()
    conn.close()

    before_cols = _columns(db_path, "contacts")
    assert before_cols == ARC1_CONTACT_COLUMNS, "фикстура должна стартовать со старой схемы, не с новой"

    Store(db_path).close()   # первый прогон: миграция
    Store(db_path).close()   # второй прогон: ОБЯЗАН быть no-op, не падать

    assert NEW_CONTACT_COLUMNS <= _columns(db_path, "contacts")

    check = sqlite3.connect(str(db_path))
    contact_row = check.execute(
        "SELECT state, paused, human_took_over FROM contacts WHERE contact_id=?",
        ("111222333",),
    ).fetchone()
    msg_rows = check.execute(
        "SELECT text FROM messages WHERE contact_id=? ORDER BY id", ("111222333",)
    ).fetchall()
    check.close()

    assert contact_row == ("active", 0, 0)
    assert [r[0] for r in msg_rows] == texts, "текст сообщений должен совпасть посимвольно, включая кириллицу"

    backups = list(db_path.parent.glob(f"{db_path.name}.pre-3a-*.bak"))
    assert len(backups) == 1, f"ожидался ровно один бэкап, получено: {backups}"
