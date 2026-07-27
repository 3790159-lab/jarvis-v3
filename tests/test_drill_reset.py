# -*- coding: utf-8 -*-
"""Сброс состояния дрил-контакта (стенд v2, Э4).

Скрипт стирает переписку — значит контракт жёстче обычного: без --apply не
меняется ничего, на клиентском контакте отказ, чужой контакт не задет, деньги
и улики (llm_usage, control_events) переживают сброс.

$0, только временная SQLite. Ни сети, ни Telethon.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_SCRIPT = _SCRIPTS / "drill_reset.py"

DRILL = "237616472:volska"
CLIENT = "999:acme"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _mod():
    return _load(_SCRIPT, "drill_reset_script")


def _db(path: Path) -> str:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE contacts (contact_id TEXT PRIMARY KEY, state TEXT NOT NULL
            DEFAULT 'new', paused INTEGER NOT NULL DEFAULT 0,
            human_took_over INTEGER NOT NULL DEFAULT 0, paused_at REAL,
            pause_source TEXT, pause_msg_id INTEGER, pause_detail TEXT,
            pause_until REAL, last_human_out_ts REAL);
        CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL,
            ts REAL NOT NULL);
        CREATE TABLE contact_profile (contact_id TEXT NOT NULL, version INTEGER
            NOT NULL, text TEXT NOT NULL, ts REAL NOT NULL,
            PRIMARY KEY (contact_id, version));
        CREATE TABLE contact_obligations (contact_id TEXT NOT NULL, okey TEXT
            NOT NULL, kind TEXT NOT NULL, owed_by TEXT NOT NULL, status TEXT
            NOT NULL, detail TEXT NOT NULL, created_msg_id INTEGER,
            closed_msg_id INTEGER, created_ts REAL NOT NULL, closed_ts REAL,
            PRIMARY KEY (contact_id, okey));
        CREATE TABLE facts (contact_id TEXT NOT NULL, key TEXT NOT NULL,
            value TEXT NOT NULL, PRIMARY KEY (contact_id, key));
        CREATE TABLE console_cards (msg_id INTEGER PRIMARY KEY, contact_id TEXT
            NOT NULL, kind TEXT NOT NULL, ts REAL NOT NULL);
        CREATE TABLE control_events (id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL, contact_id TEXT, detail TEXT, ts REAL NOT NULL);
        CREATE TABLE llm_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL
            NOT NULL, tag TEXT NOT NULL, model TEXT NOT NULL, input_tokens
            INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
            cache_read_input_tokens INTEGER NOT NULL,
            cache_creation_input_tokens INTEGER NOT NULL);
    """)
    for contact in (DRILL, CLIENT):
        conn.execute(
            "INSERT INTO contacts (contact_id, state, paused, human_took_over,"
            " paused_at, pause_source, pause_until) VALUES (?,'qualified',1,1,5.0,'stop',9.0)",
            (contact,))
        conn.execute("INSERT INTO messages (contact_id, role, text, ts)"
                     " VALUES (?,'in','привет',1.0)", (contact,))
        conn.execute("INSERT INTO contact_profile (contact_id, version, text, ts)"
                     " VALUES (?,1,'бюджет 500',1.0)", (contact,))
        conn.execute(
            "INSERT INTO contact_obligations (contact_id, okey, kind, owed_by,"
            " status, detail, created_ts) VALUES (?,'other:x','other','bot','open','d',1.0)",
            (contact,))
        conn.execute("INSERT INTO facts (contact_id, key, value)"
                     " VALUES (?,'budget','500')", (contact,))
        conn.execute("INSERT INTO console_cards (contact_id, kind, ts)"
                     " VALUES (?,'lead',1.0)", (contact,))
        conn.execute("INSERT INTO control_events (kind, contact_id, detail, ts)"
                     " VALUES ('pause',?,'d',1.0)", (contact,))
    conn.execute("INSERT INTO llm_usage (ts, tag, model, input_tokens,"
                 " output_tokens, cache_read_input_tokens,"
                 " cache_creation_input_tokens) VALUES (1.0,'brain','m',1,1,0,0)")
    conn.commit()
    conn.close()
    return str(path)


def _counts(db: str, contact: str) -> dict:
    conn = sqlite3.connect(db)
    try:
        out = {}
        for table in ("messages", "contact_profile", "contact_obligations",
                      "facts", "console_cards", "control_events"):
            out[table] = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE contact_id=?", (contact,)).fetchone()[0]
        row = conn.execute("SELECT state, paused, human_took_over, paused_at,"
                           " pause_source, pause_until FROM contacts WHERE contact_id=?",
                           (contact,)).fetchone()
        out["contact_row"] = row
        out["llm_usage"] = conn.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0]
        return out
    finally:
        conn.close()


# ── предохранитель: клиентский контакт не сбрасывается никогда ──────────────
def test_refuses_non_drill_contact(tmp_path, capsys):
    db = _db(tmp_path / "c.db")
    rc = _mod().main([db, "--contact", CLIENT, "--apply"])
    assert rc == 2
    assert "ОТКАЗ" in capsys.readouterr().out
    assert _counts(db, CLIENT)["messages"] == 1      # ничего не тронуто


def test_refuses_non_drill_contact_even_in_dry_run(tmp_path):
    # Отказ раньше любой работы: план по клиентскому контакту — тоже «нет».
    db = _db(tmp_path / "c.db")
    assert _mod().main([db, "--contact", CLIENT]) == 2


# ── dry-run по умолчанию ────────────────────────────────────────────────────
def test_dry_run_changes_nothing(tmp_path, capsys):
    db = _db(tmp_path / "c.db")
    rc = _mod().main([db, "--contact", DRILL])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ПЛАН" in out and "--apply" in out
    before = _counts(db, DRILL)
    assert before["messages"] == 1 and before["contact_obligations"] == 1


# ── собственно сброс ────────────────────────────────────────────────────────
def test_apply_wipes_drill_contact_state(tmp_path):
    db = _db(tmp_path / "c.db")
    assert _mod().main([db, "--contact", DRILL, "--apply"]) == 0
    after = _counts(db, DRILL)
    assert after["messages"] == 0
    assert after["contact_profile"] == 0
    assert after["contact_obligations"] == 0
    assert after["facts"] == 0
    assert after["console_cards"] == 0


def test_apply_resets_funnel_stage_and_pause(tmp_path):
    # Стадия воронки — то, ради чего сброс и делается; а забытый paused=1
    # означал бы, что бот молчит и весь прогон честно упрётся в таймаут.
    db = _db(tmp_path / "c.db")
    _mod().main([db, "--contact", DRILL, "--apply"])
    state, paused, took_over, paused_at, source, until = _counts(db, DRILL)["contact_row"]
    assert state == "new"
    assert paused == 0 and took_over == 0
    assert paused_at is None and source is None and until is None


def test_apply_does_not_touch_other_contacts(tmp_path):
    db = _db(tmp_path / "c.db")
    _mod().main([db, "--contact", DRILL, "--apply"])
    other = _counts(db, CLIENT)
    assert other["messages"] == 1 and other["contact_profile"] == 1
    assert other["contact_obligations"] == 1 and other["facts"] == 1
    assert other["contact_row"][0] == "qualified"


def test_money_and_control_events_survive_reset(tmp_path):
    # llm_usage — история РАСХОДОВ, control_events — улики действий владельца.
    # Сброс готовит сценарий, а не переписывает бухгалтерию и журнал.
    db = _db(tmp_path / "c.db")
    _mod().main([db, "--contact", DRILL, "--apply"])
    after = _counts(db, DRILL)
    assert after["llm_usage"] == 1
    assert after["control_events"] == 1


def test_reset_is_idempotent(tmp_path):
    db = _db(tmp_path / "c.db")
    assert _mod().main([db, "--contact", DRILL, "--apply"]) == 0
    assert _mod().main([db, "--contact", DRILL, "--apply"]) == 0   # повтор — no-op
    assert _counts(db, DRILL)["messages"] == 0


def test_prints_before_and_after(tmp_path, capsys):
    db = _db(tmp_path / "c.db")
    _mod().main([db, "--contact", DRILL, "--apply"])
    out = capsys.readouterr().out
    assert "ДО" in out and "ПОСЛЕ" in out


def test_unknown_contact_is_not_a_silent_zero(tmp_path, capsys):
    # Опечатка в contact_id не должна выглядеть успешным сбросом (DEV-18).
    db = _db(tmp_path / "c.db")
    mod = _mod()
    mod.DRILL_CONTACTS = frozenset(mod.DRILL_CONTACTS | {"237616472:typo"})
    rc = mod.main([db, "--contact", "237616472:typo", "--apply"])
    assert rc == 1
    assert "не найден" in capsys.readouterr().out


# ── список дрил-контактов один на оба скрипта ───────────────────────────────
def test_drill_contacts_list_matches_drop_phantom_script():
    # Два разных списка — это способ однажды сбросить не тот контакт: id
    # тестового аккаунта добавляется В ОБА, и сторож ловит расхождение.
    reset = _mod()
    drop = _load(_SCRIPTS / "drop_phantom_obligations.py", "drop_phantom_for_reset")
    assert reset.DRILL_CONTACTS == drop.DRILL_CONTACTS
