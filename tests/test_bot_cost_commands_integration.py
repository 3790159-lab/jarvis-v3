# -*- coding: utf-8 -*-
"""Integration: ``/my_stats`` and ``/admin_costs`` via ``process_update``.

Same fresh-module-load pattern as ``tests/test_bot_audit_integration.py``.
``send`` is patched to capture outbound replies; the cost ledger is redirected
to a tmp file via ``JARVIS_COST_FILE`` so no real state is touched.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.audit import cost_tracker

KYIV = timezone(timedelta(hours=3))


def _get_mod():
    mod_name = f"_test_cost_{id(object())}"
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
def cost_file(tmp_path, monkeypatch):
    f = tmp_path / "cost_tracking.json"
    monkeypatch.setenv("JARVIS_COST_FILE", str(f))
    return f


def test_my_stats_command_returns_user_data(cost_file, monkeypatch):
    """A whitelisted user running /my_stats sees their own spend table."""
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    monkeypatch.setenv("JARVIS_ALLOWED_USER_IDS", "222")
    cost_tracker.record_cost(222, "vasya", 4.20, timestamp=datetime(2026, 5, 27, tzinfo=KYIV))
    mod = _get_mod()

    sent: list[tuple[str, str]] = []
    upd = _text_update(user_id=222, text="/my_stats", username="vasya")
    with patch.object(mod, "send", lambda cid, txt: sent.append((str(cid), txt))):
        mod.process_update(upd)

    assert sent, "expected a reply"
    body = sent[-1][1]
    assert "Твоя статистика" in body
    assert "4.20" in body


def test_my_stats_command_no_data_yet(cost_file, monkeypatch):
    """A new whitelisted user with no batches gets the helpful prompt."""
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    monkeypatch.setenv("JARVIS_ALLOWED_USER_IDS", "333")
    mod = _get_mod()

    sent: list[tuple[str, str]] = []
    upd = _text_update(user_id=333, text="/my_stats", username="newbie")
    with patch.object(mod, "send", lambda cid, txt: sent.append((str(cid), txt))):
        mod.process_update(upd)

    assert sent
    assert "пока пусто" in sent[-1][1]


def test_admin_costs_requires_admin(cost_file, monkeypatch):
    """A non-admin whitelisted user is rejected from /admin_costs."""
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    monkeypatch.setenv("JARVIS_ALLOWED_USER_IDS", "222")
    mod = _get_mod()

    sent: list[tuple[str, str]] = []
    upd = _text_update(user_id=222, text="/admin_costs", username="vasya")
    with patch.object(mod, "send", lambda cid, txt: sent.append((str(cid), txt))):
        mod.process_update(upd)

    assert sent
    assert "только администратору" in sent[-1][1]


def test_admin_costs_admin_user_sees_table(cost_file, monkeypatch):
    """The admin running /admin_costs sees the full per-user table."""
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    cost_tracker.record_cost(222, "vasya", 1.80, timestamp=datetime(2026, 5, 27, tzinfo=KYIV))
    cost_tracker.record_cost(111, "daniil", 4.20, timestamp=datetime(2026, 5, 27, tzinfo=KYIV))
    mod = _get_mod()

    sent: list[tuple[str, str]] = []
    upd = _text_update(user_id=111, text="/admin_costs", username="daniil")
    with patch.object(mod, "send", lambda cid, txt: sent.append((str(cid), txt))):
        mod.process_update(upd)

    assert sent
    body = sent[-1][1]
    assert "Статистика по пользователям" in body
    assert "vasya" in body
    assert "daniil" in body
    assert "Активных users" in body
