from __future__ import annotations
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    contact_id TEXT PRIMARY KEY,
    state TEXT NOT NULL DEFAULT 'new',
    paused INTEGER NOT NULL DEFAULT 0,
    human_took_over INTEGER NOT NULL DEFAULT 0
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
"""

class Store:
    def __init__(self, path: str | Path):
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def get_or_create_contact(self, contact_id: str) -> dict:
        cur = self._conn.execute("SELECT * FROM contacts WHERE contact_id=?", (contact_id,))
        row = cur.fetchone()
        if row is None:
            self._conn.execute("INSERT INTO contacts(contact_id) VALUES (?)", (contact_id,))
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM contacts WHERE contact_id=?", (contact_id,)).fetchone()
        return dict(row)

    def set_state(self, contact_id: str, state: str) -> None:
        self._conn.execute("UPDATE contacts SET state=? WHERE contact_id=?", (state, contact_id))
        self._conn.commit()

    def set_flag(self, contact_id: str, flag: str, value: bool) -> None:
        if flag not in {"paused", "human_took_over"}:
            raise ValueError(f"unknown flag: {flag}")
        self._conn.execute(
            f"UPDATE contacts SET {flag}=? WHERE contact_id=?", (int(value), contact_id))
        self._conn.commit()

    def add_message(self, contact_id: str, role: str, text: str, ts: float) -> None:
        self._conn.execute(
            "INSERT INTO messages(contact_id, role, text, ts) VALUES (?,?,?,?)",
            (contact_id, role, text, ts))
        self._conn.commit()

    def history(self, contact_id: str, limit: int | None = None) -> list[dict]:
        q = "SELECT role, text, ts FROM messages WHERE contact_id=? ORDER BY id"
        rows = self._conn.execute(q, (contact_id,)).fetchall()
        rows = [dict(r) for r in rows]
        return rows[-limit:] if limit else rows

    def set_fact(self, contact_id: str, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO facts(contact_id, key, value) VALUES (?,?,?) "
            "ON CONFLICT(contact_id, key) DO UPDATE SET value=excluded.value",
            (contact_id, key, value))
        self._conn.commit()

    def facts(self, contact_id: str) -> dict:
        rows = self._conn.execute(
            "SELECT key, value FROM facts WHERE contact_id=?", (contact_id,)).fetchall()
        return {r["key"]: r["value"] for r in rows}

    def count_messages_since(self, contact_id: str, role: str, since_ts: float) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE contact_id=? AND role=? AND ts>=?",
            (contact_id, role, since_ts)).fetchone()[0]

    def count_outbound_between(self, start_ts: float, end_ts: float) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE role='assistant' AND ts>=? AND ts<?",
            (start_ts, end_ts)).fetchone()[0]
