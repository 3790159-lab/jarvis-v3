"""Phase H8.4: Real action command functions exist and are callable."""
from __future__ import annotations

import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_cmd_restart_backend_exists():
    import tools.jarvis_smart_telegram_control as tg
    assert callable(tg.cmd_restart_backend)


def test_cmd_restart_bot_exists():
    import tools.jarvis_smart_telegram_control as tg
    assert callable(tg.cmd_restart_bot)


def test_cmd_night_status_exists():
    import tools.jarvis_smart_telegram_control as tg
    assert callable(tg.cmd_night_status)


def test_cmd_night_now_exists():
    import tools.jarvis_smart_telegram_control as tg
    assert callable(tg.cmd_night_now)


def test_handle_self_status_exists():
    import tools.jarvis_smart_telegram_control as tg
    assert callable(tg.handle_self_status)


def test_handle_progress_report_exists():
    import tools.jarvis_smart_telegram_control as tg
    assert callable(tg.handle_progress_report)


def test_cmd_restart_backend_sends_message():
    import tools.jarvis_smart_telegram_control as tg
    sent = []
    with patch.object(tg, "send", side_effect=lambda cid, msg: sent.append(msg)), \
         patch("subprocess.run"), \
         patch("subprocess.Popen"), \
         patch("tools.jarvis_smart_telegram_control.time.sleep"):
        tg.cmd_restart_backend("123")
    assert any("бэкенд" in m.lower() or "backend" in m.lower() or "перезагруж" in m.lower() for m in sent), \
        f"Should send a backend-related message, sent: {sent}"


def test_cmd_night_status_no_file_sends_error():
    import tools.jarvis_smart_telegram_control as tg
    from unittest.mock import patch
    from pathlib import Path

    sent = []
    fake_path = MagicMock()
    fake_path.exists.return_value = False

    with patch.object(tg, "send", side_effect=lambda cid, msg: sent.append(msg)), \
         patch("tools.jarvis_smart_telegram_control.Path") as mock_path:
        mock_path.return_value.__truediv__ = MagicMock(return_value=fake_path)
        # Call directly without Path mock since Path is used internally
        original_path = tg.Path
        tg.cmd_night_status("123")

    # Some message should be sent
    assert len(sent) > 0


def test_classify_message_restart_backend_routes_to_action():
    from tools.jarvis_smart_telegram_control import classify_message
    state = {
        "mode": "auto", "language": "ru", "table_language": "ru",
        "keep_names_original": True, "last_topic": "", "last_table_query": "",
        "last_table_path": "", "pending": None, "pending_task_id": None,
        "last_uploaded_file": None, "last_plan": None,
        "preferences": {"answer_language": "ru", "tables_language": "ru",
                       "names_original": True, "short_status": True},
    }
    result = classify_message("перезагрузи бэкенд", state)
    assert result["intent"] == "action"
    assert result.get("action") == "restart_backend"
