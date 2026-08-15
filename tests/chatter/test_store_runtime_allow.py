"""/allow (control-bot): runtime-оверлей allowlist, персистентный в SQLite —
не зависит от settings.yaml/reload_configs (см. arc3c-contact-gate-design.md
«Out of scope» -> эта задача его закрывает)."""
from __future__ import annotations

from chatter.storage.db import Store


def test_runtime_allow_starts_empty(tmp_path):
    store = Store(tmp_path / "s.db")
    assert store.runtime_allow_ids() == []


def test_runtime_allow_add_then_list(tmp_path):
    store = Store(tmp_path / "s.db")
    store.runtime_allow_add(111, ts=1.0)
    store.runtime_allow_add(222, ts=2.0)
    assert store.runtime_allow_ids() == [111, 222]


def test_runtime_allow_add_is_idempotent(tmp_path):
    store = Store(tmp_path / "s.db")
    store.runtime_allow_add(111, ts=1.0)
    store.runtime_allow_add(111, ts=2.0)
    assert store.runtime_allow_ids() == [111]


def test_runtime_allow_remove(tmp_path):
    store = Store(tmp_path / "s.db")
    store.runtime_allow_add(111, ts=1.0)
    store.runtime_allow_add(222, ts=1.0)
    store.runtime_allow_remove(111)
    assert store.runtime_allow_ids() == [222]


def test_runtime_allow_remove_missing_is_a_noop(tmp_path):
    store = Store(tmp_path / "s.db")
    store.runtime_allow_remove(999)   # не бросает, id никогда не было
    assert store.runtime_allow_ids() == []


def test_runtime_allow_persists_across_reopen(tmp_path):
    path = tmp_path / "s.db"
    Store(path).runtime_allow_add(111, ts=1.0)
    reopened = Store(path)
    assert reopened.runtime_allow_ids() == [111]
