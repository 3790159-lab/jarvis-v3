# -*- coding: utf-8 -*-
"""Integration: ``/costs [days]`` via ``process_update`` -> ``handle_command``.

``/costs`` is pre-existing (not in ``FRIEND_ALLOWED_COMMANDS`` -> already
admin-only via the default-deny role gate in ``handle()``, covered by
existing role/whitelist infrastructure tests elsewhere); this file covers
only the new ``[days]`` argument plumbing added on top of the existing
dispatch at ``handle_command``'s ``cmd == "/costs"`` branch. Same
fresh-module-load pattern as ``tests/test_bot_cost_commands_integration.py``.
Both ledgers are redirected to tmp files (``JARVIS_COST_FILE`` /
``JARVIS_EXPENSES_FILE``) so no real state is touched.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.audit import cost_tracker


def _get_mod():
    mod_name = f"_test_costs_cmd_{id(object())}"
    spec = importlib.util.spec_from_file_location(
        mod_name, ROOT / "tools" / "jarvis_smart_telegram_control.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _text_update(user_id: int, text: str, username: str | None = "tester") -> dict:
    msg = {"from": {"id": user_id}, "chat": {"id": user_id}, "text": text}
    if username is not None:
        msg["from"]["username"] = username
    return {"update_id": 1, "message": msg}


@pytest.fixture
def ledgers(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_COST_FILE", str(tmp_path / "cost.json"))
    monkeypatch.setenv("JARVIS_EXPENSES_FILE", str(tmp_path / "expenses.jsonl"))
    return tmp_path


def test_costs_default_7_days_admin_sees_report(ledgers, monkeypatch):
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    cost_tracker.record_cost(111, "daniil", 1.50)
    mod = _get_mod()

    sent: list[tuple[str, str]] = []
    upd = _text_update(user_id=111, text="/costs", username="daniil")
    with patch.object(mod, "send", lambda cid, txt: sent.append((str(cid), txt))):
        mod.process_update(upd)

    assert sent
    body = sent[-1][1]
    assert "Траты за 7д" in body
    assert "$1.50" in body


def test_costs_custom_days_arg(ledgers, monkeypatch):
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    mod = _get_mod()

    sent: list[tuple[str, str]] = []
    upd = _text_update(user_id=111, text="/costs 14", username="daniil")
    with patch.object(mod, "send", lambda cid, txt: sent.append((str(cid), txt))):
        mod.process_update(upd)

    assert sent
    assert "Траты за 14д" in sent[-1][1]


def test_costs_invalid_days_arg_falls_back_to_default(ledgers, monkeypatch):
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    mod = _get_mod()

    sent: list[tuple[str, str]] = []
    upd = _text_update(user_id=111, text="/costs banana", username="daniil")
    with patch.object(mod, "send", lambda cid, txt: sent.append((str(cid), txt))):
        mod.process_update(upd)

    assert sent
    assert "Траты за 7д" in sent[-1][1]


def test_costs_negative_days_arg_falls_back_to_default(ledgers, monkeypatch):
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    mod = _get_mod()

    sent: list[tuple[str, str]] = []
    upd = _text_update(user_id=111, text="/costs -3", username="daniil")
    with patch.object(mod, "send", lambda cid, txt: sent.append((str(cid), txt))):
        mod.process_update(upd)

    assert sent
    assert "Траты за 7д" in sent[-1][1]
