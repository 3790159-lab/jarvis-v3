"""Retro-чистка дублирующих 'other' (scripts/cleanup_duplicate_other_obligations.py):
удаляет открытые 'other' у контактов, где открыт канонический долг (kind != other)
— retro-применение анти-фрагментации (дрил Д-10 T2 2026-07-24). Идемпотентна.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from chatter.storage.db import Store

_SCRIPT = (Path(__file__).resolve().parents[2]
           / "scripts" / "cleanup_duplicate_other_obligations.py")


def _load():
    spec = importlib.util.spec_from_file_location(
        "cleanup_duplicate_other_obligations", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cleanup_duplicate_other_obligations"] = mod
    spec.loader.exec_module(mod)
    return mod


def _insert(store, contact_id, okey, kind, status):
    store._conn.execute(
        "INSERT INTO contact_obligations(contact_id, okey, kind, owed_by, status, "
        "detail, created_msg_id, closed_msg_id, created_ts, closed_ts) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (contact_id, okey, kind, "bot", status, "d", 1, None, 1.0, None))
    store._conn.commit()


def test_cleanup_deletes_other_when_canonical_open(tmp_path):
    db = tmp_path / "t.db"
    store = Store(db)
    _insert(store, "c1", "brief", "brief", "open")
    _insert(store, "c1", "other:dup", "other", "open")     # дубль → удалить
    removed = _load().cleanup_duplicate_others(str(db))
    keys = {o.okey for o in Store(db).get_obligations("c1")}
    assert keys == {"brief"}
    assert removed == 1


def test_cleanup_keeps_other_when_no_canonical_open(tmp_path):
    db = tmp_path / "t.db"
    store = Store(db)
    _insert(store, "c2", "other:solo", "other", "open")     # одинокий other — не трогаем
    assert _load().cleanup_duplicate_others(str(db)) == 0
    assert {o.okey for o in Store(db).get_obligations("c2")} == {"other:solo"}
