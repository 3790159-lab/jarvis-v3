"""Хранилище слота обязательств (спека §3): таблица contact_obligations +
get/save/max_message_id. Полная замена набора под локом (delete+insert)."""
from __future__ import annotations

from chatter.storage.db import Store
from chatter.core.obligations_slot import merge_obligations


def _store():
    return Store(":memory:")


def _upd(kind, detail, status="open", owed_by="bot"):
    return {"kind": kind, "owed_by": owed_by, "status": status, "detail": detail}


def test_get_obligations_empty():
    assert _store().get_obligations("c1") == []


def test_save_and_get_roundtrip():
    s = _store()
    obs = merge_obligations([], [_upd("brief", "бриф")], now=1000.0, current_msg_id=5)
    s.save_obligations("c1", obs)
    got = s.get_obligations("c1")
    assert len(got) == 1
    o = got[0]
    assert o.okey == "brief" and o.kind == "brief" and o.owed_by == "bot"
    assert o.status == "open" and o.detail == "бриф"
    assert o.created_msg_id == 5 and o.created_ts == 1000.0
    assert o.closed_msg_id is None and o.closed_ts is None


def test_save_replaces_full_set_preserving_merge():
    s = _store()
    s.save_obligations("c1", merge_obligations(
        [], [_upd("brief", "a"), _upd("recalc", "b")], now=1.0, current_msg_id=1))
    merged = merge_obligations(
        s.get_obligations("c1"), [_upd("brief", "done", status="delivered")],
        now=2.0, current_msg_id=2)
    s.save_obligations("c1", merged)
    got = {o.okey: o for o in s.get_obligations("c1")}
    assert len(got) == 2
    assert got["brief"].status == "delivered" and got["brief"].closed_msg_id == 2
    assert got["recalc"].status == "open"       # неупомянутое пережило replace


def test_obligations_scoped_by_contact():
    s = _store()
    s.save_obligations("c1", merge_obligations([], [_upd("brief", "a")], now=1.0, current_msg_id=1))
    assert s.get_obligations("c2") == []


def test_max_message_id():
    s = _store()
    assert s.max_message_id("c1") is None
    s.add_message("c1", "user", "hi", 1.0)
    s.add_message("c1", "assistant", "yo", 2.0)
    s.add_message("c2", "user", "x", 3.0)
    assert s.max_message_id("c1") == 2          # AUTOINCREMENT, per-contact MAX
