# -*- coding: utf-8 -*-
"""Ручное снятие фантомных обязательств (P17): скрипт обязан быть идемпотентным
и показывать состояние ДО и ПОСЛЕ.

Пишем в ЖИВУЮ клиентскую БД — единственное место, где это разрешено, и потому
контракт жёсткий: без --apply не меняется ничего, повторный запуск ничего не
ломает, чужие обязательства не трогаются.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "drop_phantom_obligations.py"


def _load():
    spec = importlib.util.spec_from_file_location("drop_phantom_script", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["drop_phantom_script"] = mod
    spec.loader.exec_module(mod)
    return mod


def _db(path: Path, rows):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE contact_obligations (contact_id TEXT, okey TEXT, kind TEXT,
            owed_by TEXT, status TEXT, detail TEXT, created_msg_id INT,
            closed_msg_id INT, created_ts REAL, closed_ts REAL);
    """)
    conn.executemany(
        "INSERT INTO contact_obligations (contact_id, okey, kind, owed_by, status,"
        " detail, created_ts, closed_ts) VALUES (?,?,?,?,?,?,1000.0,NULL)", rows)
    conn.commit(); conn.close()
    return str(path)


def _statuses(db, contact="c"):
    conn = sqlite3.connect(db)
    try:
        return {r[0]: r[1] for r in conn.execute(
            "SELECT okey, status FROM contact_obligations WHERE contact_id=?", (contact,))}
    finally:
        conn.close()


_ROWS = [
    ("c", "other:клієнт ще не оплатив", "other", "bot", "open", "клієнт ще не оплатив"),
    ("c", "other:клієнт ще не обрав", "other", "bot", "open", "клієнт ще не обрав спосіб"),
    ("c", "brief", "brief", "bot", "delivered", "питання задані"),
    ("c", "owner_write", "owner_write", "bot", "open", "керівниця напише"),
]


def test_dry_run_changes_nothing(tmp_path, capsys):
    mod = _load()
    db = _db(tmp_path / "d.db", _ROWS)
    rc = mod.main([db, "--contact", "c", "--okey", "other:клієнт ще не оплатив"])
    out = capsys.readouterr().out
    assert rc == 0
    assert _statuses(db)["other:клієнт ще не оплатив"] == "open", "без --apply БД не меняется"
    assert "ДО" in out and "--apply" in out


def test_apply_cancels_only_the_named_obligations(tmp_path, capsys):
    mod = _load()
    db = _db(tmp_path / "d.db", _ROWS)
    rc = mod.main([db, "--contact", "c", "--apply",
                   "--okey", "other:клієнт ще не оплатив",
                   "--okey", "other:клієнт ще не обрав"])
    assert rc == 0
    st = _statuses(db)
    assert st["other:клієнт ще не оплатив"] == "cancelled"
    assert st["other:клієнт ще не обрав"] == "cancelled"
    assert st["brief"] == "delivered", "чужие обязательства не трогаем"
    assert st["owner_write"] == "open", "owner_write ведёт КОД — руками не снимаем"
    out = capsys.readouterr().out
    assert "ДО" in out and "ПОСЛЕ" in out


def test_second_run_is_a_noop(tmp_path, capsys):
    """Идемпотентность: повтор не переставляет closed_ts и не падает."""
    mod = _load()
    db = _db(tmp_path / "d.db", _ROWS)
    args = [db, "--contact", "c", "--apply", "--okey", "other:клієнт ще не оплатив"]
    mod.main(args)
    conn = sqlite3.connect(db)
    first_closed = conn.execute(
        "SELECT closed_ts FROM contact_obligations WHERE okey=?",
        ("other:клієнт ще не оплатив",)).fetchone()[0]
    conn.close()
    capsys.readouterr()

    rc = mod.main(args)
    out = capsys.readouterr().out
    assert rc == 0
    conn = sqlite3.connect(db)
    second_closed = conn.execute(
        "SELECT closed_ts FROM contact_obligations WHERE okey=?",
        ("other:клієнт ще не оплатив",)).fetchone()[0]
    conn.close()
    assert second_closed == first_closed, "повтор не должен переписывать момент закрытия"
    assert "уже снято" in out


def test_unknown_okey_is_named_not_swallowed(tmp_path, capsys):
    """Опечатка в ключе не должна выглядеть как успешная уборка (DEV-18)."""
    mod = _load()
    db = _db(tmp_path / "d.db", _ROWS)
    rc = mod.main([db, "--contact", "c", "--apply", "--okey", "other:нет-такого"])
    out = capsys.readouterr().out
    assert rc == 1, "ничего не нашли по явно названному ключу — это не успех"
    assert "не найдено" in out
