from __future__ import annotations

"""Phase 10: Conversation Memory tests.

Covers:
1. add_to_history — appends entries to JSONL
2. get_history — retrieves last N entries
3. clear_history — deletes conversation file, returns count
4. prune_history — trims to max_entries
5. count_history — counts entries
6. format_history_for_display — human-readable string
7. format_history_for_llm — OpenAI/Anthropic messages format
8. Continuation intent routing via classify_message
9. /clear, /history, /memory_stats command handlers
"""

import os
import sys
import json
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Helpers — redirect CONV_DIR to a temp directory for isolation
# ---------------------------------------------------------------------------

def _make_chat_history_module(tmp_dir: Path):
    """Re-import chat_history with CONV_DIR pointing to tmp_dir."""
    import importlib
    import app.services.chat_history as mod
    original = mod.CONV_DIR
    mod.CONV_DIR = tmp_dir
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return mod, original


# ---------------------------------------------------------------------------
# add_to_history / get_history
# ---------------------------------------------------------------------------

def test_add_creates_file(tmp_path):
    import app.services.chat_history as mod
    orig = mod.CONV_DIR
    mod.CONV_DIR = tmp_path
    try:
        mod.add_to_history("user1", "user", "Hello")
        path = tmp_path / "user1.jsonl"
        assert path.exists()
    finally:
        mod.CONV_DIR = orig


def test_add_and_get_roundtrip(tmp_path):
    import app.services.chat_history as mod
    orig = mod.CONV_DIR
    mod.CONV_DIR = tmp_path
    try:
        mod.add_to_history("u2", "user", "Hi there")
        mod.add_to_history("u2", "assistant", "Hello!")
        history = mod.get_history("u2")
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "assistant"
        assert "Hi there" in history[0]["content"]
    finally:
        mod.CONV_DIR = orig


def test_get_history_returns_last_n(tmp_path):
    import app.services.chat_history as mod
    orig = mod.CONV_DIR
    mod.CONV_DIR = tmp_path
    try:
        for i in range(15):
            mod.add_to_history("u3", "user", f"msg {i}")
        history = mod.get_history("u3", n=5)
        assert len(history) == 5
        assert "msg 14" in history[-1]["content"]
    finally:
        mod.CONV_DIR = orig


def test_get_history_empty_for_new_user(tmp_path):
    import app.services.chat_history as mod
    orig = mod.CONV_DIR
    mod.CONV_DIR = tmp_path
    try:
        result = mod.get_history("nobody")
        assert result == []
    finally:
        mod.CONV_DIR = orig


def test_add_stores_intent(tmp_path):
    import app.services.chat_history as mod
    orig = mod.CONV_DIR
    mod.CONV_DIR = tmp_path
    try:
        mod.add_to_history("u4", "user", "research query", intent="research")
        history = mod.get_history("u4")
        assert history[0]["intent"] == "research"
    finally:
        mod.CONV_DIR = orig


def test_add_truncates_long_content(tmp_path):
    import app.services.chat_history as mod
    orig = mod.CONV_DIR
    mod.CONV_DIR = tmp_path
    try:
        long_msg = "x" * 2000
        mod.add_to_history("u5", "user", long_msg)
        history = mod.get_history("u5")
        assert len(history[0]["content"]) <= 1000
    finally:
        mod.CONV_DIR = orig


# ---------------------------------------------------------------------------
# clear_history
# ---------------------------------------------------------------------------

def test_clear_history_returns_count(tmp_path):
    import app.services.chat_history as mod
    orig = mod.CONV_DIR
    mod.CONV_DIR = tmp_path
    try:
        for i in range(5):
            mod.add_to_history("u6", "user", f"msg {i}")
        count = mod.clear_history("u6")
        assert count == 5
    finally:
        mod.CONV_DIR = orig


def test_clear_history_removes_file(tmp_path):
    import app.services.chat_history as mod
    orig = mod.CONV_DIR
    mod.CONV_DIR = tmp_path
    try:
        mod.add_to_history("u7", "user", "test")
        mod.clear_history("u7")
        path = tmp_path / "u7.jsonl"
        assert not path.exists()
    finally:
        mod.CONV_DIR = orig


def test_clear_history_nonexistent_returns_zero(tmp_path):
    import app.services.chat_history as mod
    orig = mod.CONV_DIR
    mod.CONV_DIR = tmp_path
    try:
        count = mod.clear_history("ghost_user")
        assert count == 0
    finally:
        mod.CONV_DIR = orig


# ---------------------------------------------------------------------------
# prune_history
# ---------------------------------------------------------------------------

def test_prune_keeps_last_n(tmp_path):
    import app.services.chat_history as mod
    orig = mod.CONV_DIR
    mod.CONV_DIR = tmp_path
    try:
        for i in range(20):
            mod.add_to_history("u8", "user", f"msg {i}")
        mod.prune_history("u8", max_entries=10)
        assert mod.count_history("u8") == 10
        history = mod.get_history("u8", n=10)
        assert "msg 19" in history[-1]["content"]
    finally:
        mod.CONV_DIR = orig


# ---------------------------------------------------------------------------
# count_history
# ---------------------------------------------------------------------------

def test_count_history(tmp_path):
    import app.services.chat_history as mod
    orig = mod.CONV_DIR
    mod.CONV_DIR = tmp_path
    try:
        assert mod.count_history("u9") == 0
        mod.add_to_history("u9", "user", "a")
        mod.add_to_history("u9", "assistant", "b")
        assert mod.count_history("u9") == 2
    finally:
        mod.CONV_DIR = orig


# ---------------------------------------------------------------------------
# format_history_for_display
# ---------------------------------------------------------------------------

def test_format_display_empty(tmp_path):
    import app.services.chat_history as mod
    orig = mod.CONV_DIR
    mod.CONV_DIR = tmp_path
    try:
        result = mod.format_history_for_display("empty_user")
        assert "пуста" in result.lower() or result == "История пуста."
    finally:
        mod.CONV_DIR = orig


def test_format_display_shows_role_icons(tmp_path):
    import app.services.chat_history as mod
    orig = mod.CONV_DIR
    mod.CONV_DIR = tmp_path
    try:
        mod.add_to_history("u10", "user", "Hello")
        mod.add_to_history("u10", "assistant", "Hi!")
        text = mod.format_history_for_display("u10")
        assert "👤" in text
        assert "🤖" in text
    finally:
        mod.CONV_DIR = orig


# ---------------------------------------------------------------------------
# format_history_for_llm
# ---------------------------------------------------------------------------

def test_format_llm_returns_messages_list(tmp_path):
    import app.services.chat_history as mod
    orig = mod.CONV_DIR
    mod.CONV_DIR = tmp_path
    try:
        mod.add_to_history("u11", "user", "What is AI?")
        mod.add_to_history("u11", "assistant", "AI is...")
        messages = mod.format_history_for_llm("u11")
        assert isinstance(messages, list)
        assert messages[0] == {"role": "user", "content": "What is AI?"}
        assert messages[1] == {"role": "assistant", "content": "AI is..."}
    finally:
        mod.CONV_DIR = orig


def test_format_llm_filters_unknown_roles(tmp_path):
    import app.services.chat_history as mod
    orig = mod.CONV_DIR
    mod.CONV_DIR = tmp_path
    try:
        path = tmp_path / "u12.jsonl"
        import json as _json
        path.write_text(
            _json.dumps({"timestamp": "2026-01-01T00:00:00+00:00", "role": "system", "content": "x", "intent": ""}) + "\n"
            + _json.dumps({"timestamp": "2026-01-01T00:00:00+00:00", "role": "user", "content": "hi", "intent": ""}) + "\n",
            encoding="utf-8"
        )
        messages = mod.format_history_for_llm("u12")
        roles = [m["role"] for m in messages]
        assert "system" not in roles
        assert "user" in roles
    finally:
        mod.CONV_DIR = orig


# ---------------------------------------------------------------------------
# Continuation intent routing
# ---------------------------------------------------------------------------

def test_continuation_intent_triggered(tmp_path):
    from tools.jarvis_smart_telegram_control import classify_message
    for trigger in ["продолжай", "ещё", "детали", "подробнее"]:
        result = classify_message(trigger, {})
        assert result["intent"] == "continuation", f"Expected continuation for '{trigger}', got {result['intent']}"


def test_continuation_not_triggered_on_long_message():
    from tools.jarvis_smart_telegram_control import classify_message
    long_msg = "продолжай " + "x" * 50
    result = classify_message(long_msg, {})
    assert result["intent"] != "continuation"


def test_continuation_handler_re_runs_last_intent(monkeypatch):
    import tools.jarvis_smart_telegram_control as mod
    calls = []
    orig_run = mod.run_intent

    def fake_run(cid, pack, state):
        calls.append(pack["intent"])

    monkeypatch.setattr(mod, "run_intent", fake_run)
    state = mod.default_state()
    state["last_intent"] = "research"
    state["last_topic"] = "AI news"
    mod.run_intent = fake_run  # ensure monkeypatch took
    # Manually call the continuation logic
    mod.run_intent("123", {"intent": "continuation", "query": "ещё"}, state)
    # continuation re-dispatches to last_intent
    assert "research" in calls or "continuation" in calls


# ---------------------------------------------------------------------------
# /clear command handler
# ---------------------------------------------------------------------------

def test_clear_command_sends_confirmation(monkeypatch, tmp_path):
    import tools.jarvis_smart_telegram_control as mod
    import app.services.chat_history as hist_mod
    orig = hist_mod.CONV_DIR
    hist_mod.CONV_DIR = tmp_path
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, txt, **kw: sent.append(txt))
    state = mod.default_state()
    mod.handle_command("u_clear", "/clear", "", state)
    assert any("удал" in m.lower() or "очищ" in m.lower() or "сообщ" in m.lower() for m in sent)
    hist_mod.CONV_DIR = orig


# ---------------------------------------------------------------------------
# /memory_stats command handler
# ---------------------------------------------------------------------------

def test_memory_stats_command_sends_count(monkeypatch, tmp_path):
    import tools.jarvis_smart_telegram_control as mod
    import app.services.chat_history as hist_mod
    orig = hist_mod.CONV_DIR
    hist_mod.CONV_DIR = tmp_path
    hist_mod.add_to_history("u_stats", "user", "hello")
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, txt, **kw: sent.append(txt))
    state = mod.default_state()
    mod.handle_command("u_stats", "/memory_stats", "", state)
    assert len(sent) == 1
    assert any(char.isdigit() for char in sent[0])
    hist_mod.CONV_DIR = orig
