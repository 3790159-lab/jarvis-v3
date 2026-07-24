"""Retro-миграция owed_by (scripts/normalize_obligations_owed_by.py): существующие
brief/examples/recalc с owed_by=client → bot (иначе не рендерятся в brain — баг
Д-10 2026-07-24); 'other' не трогаем. Идемпотентна.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from chatter.storage.db import Store

_SCRIPT = (Path(__file__).resolve().parents[2]
           / "scripts" / "normalize_obligations_owed_by.py")


def _load():
    spec = importlib.util.spec_from_file_location(
        "normalize_obligations_owed_by", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["normalize_obligations_owed_by"] = mod
    spec.loader.exec_module(mod)
    return mod


def _insert_legacy(store, contact_id, okey, kind, owed_by):
    store._conn.execute(
        "INSERT INTO contact_obligations(contact_id, okey, kind, owed_by, status, "
        "detail, created_msg_id, closed_msg_id, created_ts, closed_ts) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (contact_id, okey, kind, owed_by, "open", "d", 1, None, 1.0, None))
    store._conn.commit()


def test_migration_normalizes_client_owned_brief_to_bot(tmp_path):
    db = tmp_path / "t.db"
    store = Store(db)
    _insert_legacy(store, "c1", "brief", "brief", "client")
    _insert_legacy(store, "c1", "other:think", "other", "client")   # НЕ трогаем
    changed = _load().normalize_owed_by(str(db))
    obs = {o.okey: o.owed_by for o in Store(db).get_obligations("c1")}
    assert obs["brief"] == "bot"
    assert obs["other:think"] == "client"
    assert changed == 1


def test_migration_idempotent(tmp_path):
    db = tmp_path / "t.db"
    store = Store(db)
    _insert_legacy(store, "c1", "recalc", "recalc", "client")
    mod = _load()
    assert mod.normalize_owed_by(str(db)) == 1
    assert mod.normalize_owed_by(str(db)) == 0
