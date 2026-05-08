"""Phase 41: Mobile-friendly UX — compact responses + quick actions."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# _compact_text
# ---------------------------------------------------------------------------

def _get_compact():
    from tools.jarvis_smart_telegram_control import _compact_text
    return _compact_text


class TestCompactText:
    def test_strips_markdown_headers(self):
        ct = _get_compact()
        text = "## Заголовок\nОдно предложение. Второе предложение."
        result = ct(text, max_sentences=5)
        assert "##" not in result
        assert "Одно предложение" in result

    def test_limits_to_two_sentences(self):
        ct = _get_compact()
        text = "Предложение первое. Предложение второе. Предложение третье. Четвёртое."
        result = ct(text, max_sentences=2)
        assert "Предложение третье" not in result
        assert "Предложение первое" in result
        assert "Предложение второе" in result

    def test_strips_table_rows(self):
        ct = _get_compact()
        text = "| Col1 | Col2 |\n| --- | --- |\nОтвет."
        result = ct(text, max_sentences=5)
        assert "|" not in result
        assert "Ответ" in result

    def test_empty_input(self):
        ct = _get_compact()
        assert ct("", max_sentences=2) == ""

    def test_single_sentence_unchanged(self):
        ct = _get_compact()
        text = "Одно предложение."
        assert ct(text, max_sentences=2) == "Одно предложение."

    def test_max_sentences_one(self):
        ct = _get_compact()
        text = "Первое. Второе. Третье."
        result = ct(text, max_sentences=1)
        assert "Первое." in result
        assert "Второе" not in result


# ---------------------------------------------------------------------------
# send_with_feedback — quick action keyboard
# ---------------------------------------------------------------------------

class TestSendWithFeedbackQuickActions:
    def _get_mod(self):
        mod_name = f"_test_mobile_ux_{id(self)}"
        spec = importlib.util.spec_from_file_location(
            mod_name, ROOT / "tools" / "jarvis_smart_telegram_control.py"
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        return mod

    def test_quick_actions_added_to_keyboard(self):
        mod = self._get_mod()
        sent = []

        def fake_send(cid, text, reply_markup=None):
            sent.append(reply_markup)

        with patch.object(mod, "send", fake_send):
            # Mock make_feedback_keyboard to return basic kb
            with patch.dict("sys.modules", {
                "app.services.decision_log": MagicMock(
                    make_feedback_keyboard=lambda d: {
                        "inline_keyboard": [[
                            {"text": "👍", "callback_data": f"feedback:positive:{d}"},
                            {"text": "👎", "callback_data": f"feedback:negative:{d}"},
                        ]]
                    }
                )
            }):
                mod.send_with_feedback("123", "Test answer", "dec_123", query="test query")

        assert sent, "send should be called"
        kb = sent[0]
        assert kb is not None
        rows = kb["inline_keyboard"]
        # First row: 👍 👎
        assert any(btn["text"] == "👍" for btn in rows[0])
        assert any(btn["text"] == "👎" for btn in rows[0])

    def test_quick_action_row_has_more_button(self):
        mod = self._get_mod()
        sent = []

        def fake_send(cid, text, reply_markup=None):
            sent.append(reply_markup)

        with patch.object(mod, "send", fake_send):
            with patch.dict("sys.modules", {
                "app.services.decision_log": MagicMock(
                    make_feedback_keyboard=lambda d: {"inline_keyboard": [[
                        {"text": "👍", "callback_data": f"feedback:positive:{d}"},
                        {"text": "👎", "callback_data": f"feedback:negative:{d}"},
                    ]]}
                )
            }):
                mod.send_with_feedback("123", "Test", "dec_456", query="what is AI")

        kb = sent[0]["inline_keyboard"]
        all_buttons = [btn for row in kb for btn in row]
        texts = [b["text"] for b in all_buttons]
        assert "🔄 Подробнее" in texts

    def test_obsidian_button_always_present(self):
        mod = self._get_mod()
        sent = []

        def fake_send(cid, text, reply_markup=None):
            sent.append(reply_markup)

        with patch.object(mod, "send", fake_send):
            with patch.dict("sys.modules", {
                "app.services.decision_log": MagicMock(
                    make_feedback_keyboard=lambda d: {"inline_keyboard": [[
                        {"text": "👍", "callback_data": "feedback:positive:x"},
                        {"text": "👎", "callback_data": "feedback:negative:x"},
                    ]]}
                )
            }):
                mod.send_with_feedback("123", "Test", "dec_789")  # no query

        kb = sent[0]["inline_keyboard"]
        all_buttons = [btn for row in kb for btn in row]
        texts = [b["text"] for b in all_buttons]
        assert "📋 В Obsidian" in texts

    def test_sends_plain_when_no_decision_id(self):
        mod = self._get_mod()
        sent = []
        with patch.object(mod, "send", lambda cid, text, reply_markup=None: sent.append((text, reply_markup))):
            mod.send_with_feedback("123", "Simple answer", None)
        assert sent
        assert sent[0][1] is None  # no keyboard


# ---------------------------------------------------------------------------
# Callback QA more / obsidian
# ---------------------------------------------------------------------------

class TestQACallbacks:
    def _make_cq(self, data: str, msg_text: str = "") -> dict:
        return {
            "id": "cq_test",
            "data": data,
            "message": {
                "message_id": 42,
                "text": msg_text,
                "chat": {"id": 111},
            }
        }

    def _get_mod(self):
        mod_name = f"_test_qa_cb_{id(self)}"
        spec = importlib.util.spec_from_file_location(
            mod_name, ROOT / "tools" / "jarvis_smart_telegram_control.py"
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        return mod

    def test_qa_more_calls_research(self):
        mod = self._get_mod()
        intents = []
        with patch.object(mod, "answer_callback_query"), \
             patch.object(mod, "run_intent", lambda cid, pack, st: intents.append(pack["intent"])):
            mod.handle_callback_query(self._make_cq("qa:more:dec_abc", "🔎 Результат:\nTest"), {})
        assert intents == ["research"]

    def test_qa_obsidian_calls_backend(self):
        mod = self._get_mod()
        posts = []
        with patch.object(mod, "answer_callback_query"), \
             patch.object(mod, "send"), \
             patch.object(mod, "backend_post", lambda path, payload, **kw: posts.append(path) or {"path": "note.md"}):
            mod.handle_callback_query(self._make_cq("qa:obsidian:dec_xyz", "Some content"), {})
        assert any("obsidian" in p for p in posts)
