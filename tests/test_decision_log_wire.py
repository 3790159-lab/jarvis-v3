"""Phase 34: Tests for decision_log wire-up in run_intent + feedback keyboards."""
from __future__ import annotations

import sys
import time
import json
from pathlib import Path
from unittest.mock import patch, MagicMock, call

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state():
    return {"mode": "auto"}


def _mock_backend(return_value=None):
    """Patch backend_post to return given dict."""
    if return_value is None:
        return_value = {"answer": "Test answer", "ok": True}
    return patch(
        "tools.jarvis_smart_telegram_control.backend_post",
        return_value=return_value,
    )


def _mock_send():
    sent = []
    def _fake(cid, text, **kw):
        sent.append({"chat_id": cid, "text": text, "kwargs": kw})
    return sent, patch("tools.jarvis_smart_telegram_control.send", side_effect=_fake)


# ---------------------------------------------------------------------------
# _try_log_decision
# ---------------------------------------------------------------------------

class TestTryLogDecision:
    def test_returns_decision_id(self, tmp_path):
        from app.services.decision_log import DECISIONS_PATH
        import tools.jarvis_smart_telegram_control as ctrl
        with patch("app.services.decision_log.DECISIONS_PATH", tmp_path / "d.jsonl"):
            decision_id = ctrl._try_log_decision("test query", "research", time.time())
        assert decision_id is not None
        assert isinstance(decision_id, str)
        assert len(decision_id) == 16

    def test_writes_to_log(self, tmp_path):
        import tools.jarvis_smart_telegram_control as ctrl
        log_path = tmp_path / "d.jsonl"
        with patch("app.services.decision_log.DECISIONS_PATH", log_path):
            ctrl._try_log_decision("my question", "brain", time.time())
        assert log_path.exists()
        record = json.loads(log_path.read_text().strip())
        assert record["intent_chosen"] == "brain"
        assert record["user_query"] == "my question"

    def test_returns_none_on_import_error(self):
        import tools.jarvis_smart_telegram_control as ctrl
        with patch.dict("sys.modules", {"app.services.decision_log": None}):
            result = ctrl._try_log_decision("q", "research", time.time())
        assert result is None


# ---------------------------------------------------------------------------
# send_with_feedback
# ---------------------------------------------------------------------------

class TestSendWithFeedback:
    def test_sends_message_with_keyboard_when_decision_id(self, tmp_path):
        sent = []
        def _fake_send(cid, text, reply_markup=None):
            sent.append({"text": text, "reply_markup": reply_markup})

        import tools.jarvis_smart_telegram_control as ctrl
        import app.services.decision_log as dl
        with patch("app.services.decision_log.DECISIONS_PATH", tmp_path / "d.jsonl"):
            did = dl.log_decision("q", "research", "research")
        with patch("tools.jarvis_smart_telegram_control.send", side_effect=_fake_send):
            with patch("app.services.decision_log.DECISIONS_PATH", tmp_path / "d.jsonl"):
                ctrl.send_with_feedback("123", "Result text", did)
        assert len(sent) == 1
        assert sent[0]["text"] == "Result text"
        assert sent[0]["reply_markup"] is not None
        kb = sent[0]["reply_markup"]
        assert "inline_keyboard" in kb
        buttons = kb["inline_keyboard"][0]
        assert any("👍" in b["text"] for b in buttons)
        assert any("👎" in b["text"] for b in buttons)

    def test_sends_plain_without_decision_id(self):
        sent = []
        import tools.jarvis_smart_telegram_control as ctrl
        with patch("tools.jarvis_smart_telegram_control.send", side_effect=lambda cid, txt, **kw: sent.append(kw)):
            ctrl.send_with_feedback("123", "Hello", None)
        assert len(sent) == 1

    def test_no_crash_when_decision_log_fails(self):
        import tools.jarvis_smart_telegram_control as ctrl
        with patch("tools.jarvis_smart_telegram_control.send") as mock_send:
            with patch.dict("sys.modules", {"app.services.decision_log": None}):
                ctrl.send_with_feedback("123", "text", "fake_id")
        mock_send.assert_called_once()


# ---------------------------------------------------------------------------
# run_intent: decision logging
# ---------------------------------------------------------------------------

class TestRunIntentDecisionLogging:
    def test_state_gets_last_decision_id_for_research(self, tmp_path):
        import tools.jarvis_smart_telegram_control as ctrl
        state = _make_state()
        log_path = tmp_path / "d.jsonl"
        with patch("app.services.decision_log.DECISIONS_PATH", log_path):
            with _mock_backend({"answer": "result"}):
                with patch("tools.jarvis_smart_telegram_control.send"):
                    with patch("tools.jarvis_smart_telegram_control.send_with_feedback"):
                        ctrl.run_intent("123", {"intent": "research", "query": "test"}, state)
        assert "last_decision_id" in state

    def test_state_gets_last_decision_id_for_simple_question(self, tmp_path):
        import tools.jarvis_smart_telegram_control as ctrl
        state = _make_state()
        log_path = tmp_path / "d.jsonl"
        with patch("app.services.decision_log.DECISIONS_PATH", log_path):
            with patch("tools.jarvis_smart_telegram_control.send"):
                with patch("tools.jarvis_smart_telegram_control.send_with_feedback"):
                    with patch("app.services.quick_answer.quick_answer", return_value="Quick answer"):
                        ctrl.run_intent("123", {"intent": "simple_question", "query": "what is AI"}, state)
        assert "last_decision_id" in state

    def test_greeting_no_decision_id(self):
        import tools.jarvis_smart_telegram_control as ctrl
        state = _make_state()
        with patch("tools.jarvis_smart_telegram_control.send"):
            ctrl.run_intent("123", {"intent": "greeting"}, state)
        # For greeting intent, decision_id is None (not logged)
        assert state.get("last_decision_id") is None

    def test_decision_written_to_log(self, tmp_path):
        import tools.jarvis_smart_telegram_control as ctrl
        state = _make_state()
        log_path = tmp_path / "d.jsonl"
        with patch("app.services.decision_log.DECISIONS_PATH", log_path):
            with _mock_backend({"answer": "research result"}):
                with patch("tools.jarvis_smart_telegram_control.send"):
                    with patch("tools.jarvis_smart_telegram_control.send_with_feedback"):
                        ctrl.run_intent("123", {"intent": "research", "query": "найди информацию"}, state)
        if log_path.exists():
            record = json.loads(log_path.read_text(encoding="utf-8").strip())
            assert record["intent_chosen"] == "research"
            assert len(record["user_query"]) > 0

    def test_research_uses_send_with_feedback(self, tmp_path):
        import tools.jarvis_smart_telegram_control as ctrl
        state = _make_state()
        log_path = tmp_path / "d.jsonl"
        swf_calls = []
        with patch("app.services.decision_log.DECISIONS_PATH", log_path):
            with _mock_backend({"answer": "research result"}):
                with patch("tools.jarvis_smart_telegram_control.send"):
                    with patch(
                        "tools.jarvis_smart_telegram_control.send_with_feedback",
                        side_effect=lambda cid, txt, did, **kw: swf_calls.append(did),
                    ):
                        # /research is PAID → money-gated; token = post-confirm.
                        state["_paid_confirmed"] = "/research"
                        ctrl.run_intent("123", {"intent": "research", "query": "test"}, state)
        assert len(swf_calls) == 1

    def test_simple_question_uses_send_with_feedback(self, tmp_path):
        import tools.jarvis_smart_telegram_control as ctrl
        state = _make_state()
        log_path = tmp_path / "d.jsonl"
        swf_calls = []
        with patch("app.services.decision_log.DECISIONS_PATH", log_path):
            with patch("tools.jarvis_smart_telegram_control.send"):
                with patch(
                    "tools.jarvis_smart_telegram_control.send_with_feedback",
                    side_effect=lambda cid, txt, did, **kw: swf_calls.append(did),
                ):
                    with patch("app.services.quick_answer.quick_answer", return_value="Quick answer"):
                        ctrl.run_intent("123", {"intent": "simple_question", "query": "what"}, state)
        assert len(swf_calls) == 1


# ---------------------------------------------------------------------------
# make_feedback_keyboard
# ---------------------------------------------------------------------------

class TestMakeFeedbackKeyboard:
    def test_keyboard_structure(self):
        from app.services.decision_log import make_feedback_keyboard
        kb = make_feedback_keyboard("abc123")
        assert "inline_keyboard" in kb
        row = kb["inline_keyboard"][0]
        assert len(row) == 2
        thumbs_up = next((b for b in row if "👍" in b["text"]), None)
        thumbs_down = next((b for b in row if "👎" in b["text"]), None)
        assert thumbs_up is not None
        assert thumbs_down is not None

    def test_callback_data_contains_decision_id(self):
        from app.services.decision_log import make_feedback_keyboard
        kb = make_feedback_keyboard("myid123")
        row = kb["inline_keyboard"][0]
        all_callbacks = [b["callback_data"] for b in row]
        assert any("myid123" in c for c in all_callbacks)

    def test_callback_data_format(self):
        from app.services.decision_log import make_feedback_keyboard, parse_feedback_callback
        kb = make_feedback_keyboard("testid")
        row = kb["inline_keyboard"][0]
        for btn in row:
            parsed = parse_feedback_callback(btn["callback_data"])
            assert parsed is not None
            assert parsed["decision_id"] == "testid"
            assert parsed["type"] in ("positive", "negative")
