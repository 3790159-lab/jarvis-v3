# -*- coding: utf-8 -*-
"""Belt-and-suspenders: every remaining Telegram network egress point must be
silenced under pytest so regress/target runs never leak into the live chat."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import app.services.jarvis_telegram_file_tools as file_tools
from app.services.jarvis_operator_task_center import JarvisOperatorTaskCenter


def test_send_document_suppressed_under_pytest(monkeypatch, tmp_path):
    monkeypatch.delenv("JARVIS_ALLOW_TELEGRAM_SEND", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok123456")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "123")
    calls = {"n": 0}

    def spy(*a, **k):
        calls["n"] += 1
        return MagicMock()

    with patch.object(file_tools.urllib.request, "urlopen", spy):
        result = file_tools._send_telegram_document(str(tmp_path / "nope.csv"), "cap")

    assert calls["n"] == 0
    assert result["ok"] is False


def test_operator_send_message_suppressed_under_pytest(monkeypatch, tmp_path):
    monkeypatch.delenv("JARVIS_ALLOW_TELEGRAM_SEND", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok123456")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "123")
    calls = {"n": 0}

    def spy(*a, **k):
        calls["n"] += 1
        return MagicMock()

    center = JarvisOperatorTaskCenter(tmp_path)
    with patch("urllib.request.urlopen", spy):
        result = center.send_telegram_message("phantom operator report")

    assert calls["n"] == 0
    assert result["ok"] is False
