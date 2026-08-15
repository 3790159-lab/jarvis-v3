"""Предохранитель тестовых активов: test-реквизиты и заглушки объёма НИКОГДА
не доезжают до живого контакта (решение владельца 2026-08-10).

Класс риска здесь тот же, что у дрил-контактов вообще: тестовый актив, утёкший
в живой диалог, — это либо деньги на наш тестовый счёт вместо клиентского
(невозвратно и виноваты мы), либо реплика-заглушка живому лиду.

Отказ в выдаче — ЯВНЫЙ (исключение), а не пустая строка: подставить «что
попало» хуже, чем промолчать, и молчаливый отказ неотличим от успеха (DEV-18).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from chatter.payments.drill_gate import DRILL_CONTACTS, NotForProduction, guard_test_asset

LIVE = "555000111:volska"          # живой лид, не дрил
DRILL = "8849893367:volska"        # дрил-контакт из канона


def test_drill_set_is_explicit_and_small():
    """Потолок, а не РАВЕНСТВО: точное число живёт в каноне
    (`scripts/drill_reset.py`), и продублировать его здесь значит завести два
    числа на одну вещь. Так и вышло 14.08 — законная правка канона (персона
    yarina) уронила этот сторож, и красным он был НЕ про то, что охраняет.
    Охраняет он «список короткий и явный», а не «в нём ровно два элемента»."""
    assert DRILL in DRILL_CONTACTS
    assert LIVE not in DRILL_CONTACTS
    assert len(DRILL_CONTACTS) <= 4, sorted(DRILL_CONTACTS)


def test_canonical_list_is_the_same_as_the_scripts_one():
    """Пятый потребитель одного списка. Разошедшиеся списки — способ однажды
    выдать тестовый IBAN живому клиенту (сторож-близнец:
    tests/test_drill_contacts_sync.py)."""
    path = Path(__file__).resolve().parents[2] / "scripts" / "drill_reset.py"
    spec = importlib.util.spec_from_file_location("drill_reset_for_payments", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert DRILL_CONTACTS == mod.DRILL_CONTACTS


def test_gate_passes_drill_contact():
    guard_test_asset(contact_id=DRILL, what="test-реквизиты")


def test_gate_refuses_live_contact_loudly():
    with pytest.raises(NotForProduction, match="test-реквизиты"):
        guard_test_asset(contact_id=LIVE, what="test-реквизиты")


def test_gate_refuses_empty_or_unknown_contact():
    """Пустой contact_id не должен считаться «ну наверное дрил»."""
    for cid in ("", None, "8849893367", "8849893367:demo"):
        with pytest.raises(NotForProduction):
            guard_test_asset(contact_id=cid, what="заглушка объёма")


def test_gate_cannot_be_widened_by_the_caller():
    """Список дрил-контактов — не параметр вызова. Если бы его передавали
    аргументом, любой вызыватель мог бы открыть шлюз, передав своё множество."""
    import inspect
    params = set(inspect.signature(guard_test_asset).parameters)
    assert params == {"contact_id", "what"}
