"""Phase H9.3: Fix decision log import in progress_report."""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_get_recent_decisions_importable():
    from app.services.decision_log import get_recent_decisions
    assert callable(get_recent_decisions)


def test_list_decisions_does_not_exist():
    """list_decisions never existed — importing it must raise ImportError."""
    import importlib
    try:
        from app.services.decision_log import list_decisions  # type: ignore[attr-defined]
        assert False, "list_decisions should not exist"
    except ImportError:
        pass


def test_get_recent_decisions_returns_list():
    from app.services.decision_log import get_recent_decisions
    result = get_recent_decisions(n=5)
    assert isinstance(result, list)


def test_handle_progress_report_no_import_error(monkeypatch):
    """handle_progress_report should not raise ImportError on list_decisions."""
    import tools.jarvis_smart_telegram_control as mod
    sent_msgs = []
    monkeypatch.setattr(mod, "send", lambda chat_id, text, **kw: sent_msgs.append(text))
    # Should not raise
    mod.handle_progress_report("test_chat")
    assert len(sent_msgs) >= 1


def test_progress_report_contains_decisions_line(monkeypatch):
    """Progress report output should include 'Решений сегодня' line."""
    import tools.jarvis_smart_telegram_control as mod
    captured = []
    monkeypatch.setattr(mod, "send", lambda chat_id, text, **kw: captured.append(text))
    mod.handle_progress_report("test_chat")
    assert any("Решений сегодня" in m or "ошибка" in m for m in captured), \
        f"Expected decisions info, got: {captured}"


def test_bot_file_uses_get_recent_decisions_not_list_decisions():
    """Ensure the bot source code uses get_recent_decisions."""
    import pathlib
    src = pathlib.Path("tools/jarvis_smart_telegram_control.py").read_text(encoding="utf-8")
    assert "list_decisions" not in src, "list_decisions still found in bot source"
    assert "get_recent_decisions" in src, "get_recent_decisions not found in bot source"
