from __future__ import annotations
import shutil
import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    contact_id TEXT PRIMARY KEY,
    state TEXT NOT NULL DEFAULT 'new',
    paused INTEGER NOT NULL DEFAULT 0,
    human_took_over INTEGER NOT NULL DEFAULT 0,
    paused_at REAL,
    pause_source TEXT,
    pause_msg_id INTEGER,
    pause_detail TEXT,
    pause_until REAL,
    last_human_out_ts REAL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (
    contact_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (contact_id, key)
);
CREATE TABLE IF NOT EXISTS runtime_flags (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS console_cards (
    msg_id INTEGER PRIMARY KEY,
    contact_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS control_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    contact_id TEXT,
    detail TEXT,
    ts REAL NOT NULL
);
"""

# Колонки, которых нет в базах арки 1/2. CREATE TABLE IF NOT EXISTS не добавляет
# колонки в СУЩЕСТВУЮЩУЮ таблицу — старая база получит их только через ALTER.
_ADDED_COLUMNS = {
    "contacts": {
        "paused_at": "REAL",
        "pause_source": "TEXT",
        "pause_msg_id": "INTEGER",
        "pause_detail": "TEXT",
        "pause_until": "REAL",
        "last_human_out_ts": "REAL",
    },
}

class Store:
    """One SQLite file, safe to use across threads.

    The Telethon transport runs `process_batch` in worker threads
    (`asyncio.to_thread`), so the connection is opened with
    `check_same_thread=False` and every operation is serialized under a lock.
    Without this, SQLite raises "objects created in a thread can only be used
    in that same thread" the moment a reply is processed off the event loop.
    (The synchronous fake-console transport and the unit tests all run on one
    thread, which is why this only surfaced under the live async transport.)
    """

    def __init__(self, path: str | Path):
        path = Path(path)
        # ':memory:' не файл — Path(':memory:').exists() корректно даёт False,
        # а str(Path(':memory:')) == ':memory:', так что sqlite3.connect
        # по-прежнему получает in-memory базу, а не создаёт файл на диске.
        pre_existing = path.exists()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
            missing = self._missing_columns()
            if missing:
                # Бэкап ТОЛЬКО когда реально мигрируем существующую базу:
                # иначе каждый рестарт раннера сыпал бы .bak-файлы клиенту.
                if pre_existing:
                    self._backup(path)
                self._apply_migration(missing)

    def _missing_columns(self) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        for table, cols in _ADDED_COLUMNS.items():
            have = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            gap = {c: decl for c, decl in cols.items() if c not in have}
            if gap:
                out[table] = gap
        return out

    def _backup(self, path: Path) -> None:
        dest = path.with_name(f"{path.name}.pre-3a-{int(time.time())}.bak")
        shutil.copy2(path, dest)

    def _apply_migration(self, missing: dict[str, dict[str, str]]) -> None:
        # Идемпотентность через ПРОВЕРКУ наличия колонки, а не через ловлю
        # исключения: гардиан перезапускает раннер постоянно, и миграция,
        # падающая на втором прогоне, = краш-петля, которую гардиан будет
        # вечно поддерживать.
        for table, cols in missing.items():
            for col, decl in cols.items():
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def get_or_create_contact(self, contact_id: str) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM contacts WHERE contact_id=?", (contact_id,)).fetchone()
            if row is None:
                self._conn.execute("INSERT INTO contacts(contact_id) VALUES (?)", (contact_id,))
                self._conn.commit()
                row = self._conn.execute(
                    "SELECT * FROM contacts WHERE contact_id=?", (contact_id,)).fetchone()
            return dict(row)

    def set_state(self, contact_id: str, state: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE contacts SET state=? WHERE contact_id=?", (state, contact_id))
            self._conn.commit()

    def set_flag(self, contact_id: str, flag: str, value: bool) -> None:
        if flag not in {"paused", "human_took_over"}:
            raise ValueError(f"unknown flag: {flag}")
        with self._lock:
            self._conn.execute(
                f"UPDATE contacts SET {flag}=? WHERE contact_id=?", (int(value), contact_id))
            self._conn.commit()

    def add_message(self, contact_id: str, role: str, text: str, ts: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages(contact_id, role, text, ts) VALUES (?,?,?,?)",
                (contact_id, role, text, ts))
            self._conn.commit()

    def history(self, contact_id: str, limit: int | None = None) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, text, ts FROM messages WHERE contact_id=? ORDER BY id",
                (contact_id,)).fetchall()
        rows = [dict(r) for r in rows]
        if limit is None:
            return rows
        return rows[-limit:] if limit else []

    def set_fact(self, contact_id: str, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO facts(contact_id, key, value) VALUES (?,?,?) "
                "ON CONFLICT(contact_id, key) DO UPDATE SET value=excluded.value",
                (contact_id, key, value))
            self._conn.commit()

    def facts(self, contact_id: str) -> dict:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value FROM facts WHERE contact_id=?", (contact_id,)).fetchall()
        return {r["key"]: r["value"] for r in rows}

    def count_messages_since(self, contact_id: str, role: str, since_ts: float) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE contact_id=? AND role=? AND ts>=?",
                (contact_id, role, since_ts)).fetchone()[0]

    def count_outbound_between(self, start_ts: float, end_ts: float) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE role='assistant' AND ts>=? AND ts<?",
                (start_ts, end_ts)).fetchone()[0]
