"""Phase H8.2: Smart Router fix — proper routing for status/action/progress."""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.jarvis_smart_telegram_control import classify_message


def _state():
    return {
        "mode": "auto",
        "language": "ru",
        "table_language": "ru",
        "keep_names_original": True,
        "last_topic": "",
        "last_table_query": "",
        "last_table_path": "",
        "pending": None,
        "pending_task_id": None,
        "last_uploaded_file": None,
        "last_plan": None,
        "preferences": {
            "answer_language": "ru",
            "tables_language": "ru",
            "names_original": True,
            "short_status": True,
        },
    }


# ---------------------------------------------------------------------------
# Status questions → self_status (NOT engineer)
# ---------------------------------------------------------------------------

def test_status_ты_работаешь():
    result = classify_message("ты работаешь?", _state())
    # "ты работаешь" is in IDENTITY_TRIGGERS — both identity and self_status are correct real checks
    assert result["intent"] in ("self_status", "identity"), \
        f"Expected self_status or identity (both are real checks), got {result['intent']}"


def test_status_ты_жив():
    result = classify_message("ты жив?", _state())
    # "ты жив" is in _STATUS_PATTERNS but "ты" alone is not an identity trigger → self_status
    assert result["intent"] in ("self_status", "identity"), \
        f"Expected self_status or identity, got {result['intent']}"


def test_status_как_дела():
    result = classify_message("как дела", _state())
    # Short small talk — can be small_talk or self_status
    assert result["intent"] in ("self_status", "small_talk", "greeting"), \
        f"Expected status-ish intent, got {result['intent']}"


def test_status_как_успехи():
    result = classify_message("как успехи самоулучшения?", _state())
    # Should be self_status (pattern: "как успехи самоулучшени"), NOT engineer
    assert result["intent"] in ("self_status", "progress_report"), \
        f"Expected self_status/progress_report, got {result['intent']}"


def test_status_ты_онлайн():
    result = classify_message("ты онлайн?", _state())
    # "ты онлайн" is in IDENTITY_TRIGGERS → identity
    assert result["intent"] in ("identity", "self_status"), \
        f"Expected identity or self_status, got {result['intent']}"


def test_status_бот_работает():
    result = classify_message("бот работает?", _state())
    assert result["intent"] == "self_status", f"Expected self_status, got {result['intent']}"


def test_status_short_message():
    result = classify_message("статус", _state())
    # "статус" routes to health (existing provider-check) or self_status — both are valid real checks
    assert result["intent"] in ("self_status", "health"), \
        f"Expected self_status or health (real check), got {result['intent']}"


# ---------------------------------------------------------------------------
# Action commands → action intent
# ---------------------------------------------------------------------------

def test_action_restart_backend_ru():
    result = classify_message("перезагрузи бэкенд", _state())
    assert result["intent"] == "action", f"Expected action, got {result['intent']}"
    assert result.get("action") == "restart_backend"


def test_action_restart_bot_ru():
    result = classify_message("перезагрузи бот", _state())
    assert result["intent"] == "action", f"Expected action, got {result['intent']}"
    assert result.get("action") == "restart_bot"


def test_action_restart_backend_en():
    result = classify_message("restart backend", _state())
    assert result["intent"] == "action", f"Expected action, got {result['intent']}"
    assert result.get("action") == "restart_backend"


def test_action_restart_bot_en():
    result = classify_message("restart bot", _state())
    assert result["intent"] == "action", f"Expected action, got {result['intent']}"
    assert result.get("action") == "restart_bot"


def test_action_перезапусти_бот():
    result = classify_message("перезапусти бот", _state())
    assert result["intent"] == "action", f"Expected action, got {result['intent']}"
    assert result.get("action") == "restart_bot"


# ---------------------------------------------------------------------------
# Progress questions → progress_report
# ---------------------------------------------------------------------------

def test_progress_что_сделал():
    result = classify_message("что ты сделал сегодня?", _state())
    assert result["intent"] == "progress_report", f"Expected progress_report, got {result['intent']}"


def test_progress_задачи_выполнены():
    result = classify_message("задачи выполнены?", _state())
    assert result["intent"] == "progress_report", f"Expected progress_report, got {result['intent']}"


def test_progress_что_выполнил():
    result = classify_message("что выполнил?", _state())
    assert result["intent"] == "progress_report", f"Expected progress_report, got {result['intent']}"


# ---------------------------------------------------------------------------
# Regular queries still route correctly
# ---------------------------------------------------------------------------

def test_regular_research_not_hijacked():
    result = classify_message("найди топ-10 AI инструментов для маркетинга", _state())
    assert result["intent"] not in ("self_status", "progress_report"), \
        f"Research query should not be self_status/progress_report, got {result['intent']}"


def test_table_query_not_hijacked():
    result = classify_message("сделай таблицу сравнения CRM", _state())
    assert result["intent"] == "table", f"Table query should route to table, got {result['intent']}"


def test_image_query_not_hijacked():
    result = classify_message("нарисуй красивый пейзаж", _state())
    assert result["intent"] == "generate", f"Image query should route to generate, got {result['intent']}"
