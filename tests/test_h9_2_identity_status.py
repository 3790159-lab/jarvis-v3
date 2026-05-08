"""Phase H9.2: Status check priority over identity for production status questions."""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.jarvis_smart_telegram_control import classify_message, IDENTITY_TRIGGERS


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
# "ты работаешь?" → self_status (NOT identity)
# ---------------------------------------------------------------------------

def test_ti_rabotaesh_routes_to_self_status():
    result = classify_message("ты работаешь?", _state())
    assert result["intent"] == "self_status", (
        f"Expected self_status, got {result['intent']}"
    )


def test_ti_zhiv_routes_to_self_status():
    result = classify_message("ты жив?", _state())
    assert result["intent"] == "self_status", (
        f"Expected self_status, got {result['intent']}"
    )


def test_ti_onlain_routes_to_self_status():
    result = classify_message("ты онлайн?", _state())
    assert result["intent"] == "self_status", (
        f"Expected self_status, got {result['intent']}"
    )


def test_ti_tut_routes_to_self_status():
    result = classify_message("ты тут?", _state())
    assert result["intent"] == "self_status", (
        f"Expected self_status, got {result['intent']}"
    )


def test_are_you_there_routes_to_self_status():
    result = classify_message("are you there?", _state())
    assert result["intent"] == "self_status", (
        f"Expected self_status, got {result['intent']}"
    )


def test_are_you_online_routes_to_self_status():
    result = classify_message("are you online?", _state())
    assert result["intent"] == "self_status", (
        f"Expected self_status, got {result['intent']}"
    )


# ---------------------------------------------------------------------------
# Pure identity questions still go to identity
# ---------------------------------------------------------------------------

def test_kto_ti_routes_to_identity():
    result = classify_message("кто ты?", _state())
    assert result["intent"] == "identity", (
        f"Expected identity, got {result['intent']}"
    )


def test_chto_ti_takoe_routes_to_identity():
    result = classify_message("что ты такое?", _state())
    assert result["intent"] == "identity", (
        f"Expected identity, got {result['intent']}"
    )


def test_predstavis_routes_to_identity():
    result = classify_message("представься", _state())
    assert result["intent"] == "identity", (
        f"Expected identity, got {result['intent']}"
    )


def test_kak_tebya_zovut_routes_to_identity():
    result = classify_message("как тебя зовут?", _state())
    assert result["intent"] == "identity", (
        f"Expected identity, got {result['intent']}"
    )


# ---------------------------------------------------------------------------
# IDENTITY_TRIGGERS does NOT contain problematic presence triggers
# ---------------------------------------------------------------------------

def test_identity_triggers_no_rabotaesh():
    assert "ты работаешь" not in IDENTITY_TRIGGERS


def test_identity_triggers_no_onlain():
    assert "ты онлайн" not in IDENTITY_TRIGGERS
    assert "онлайн" not in IDENTITY_TRIGGERS


def test_identity_triggers_no_are_you_there():
    assert "are you there" not in IDENTITY_TRIGGERS


def test_identity_triggers_no_zhivoy():
    assert "живой" not in IDENTITY_TRIGGERS
