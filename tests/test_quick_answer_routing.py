"""Tests for Phase 23: Smart Question Routing + Image Gen UX Fix."""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.quick_answer import is_simple_question, quick_answer


# ─── is_simple_question ──────────────────────────────────────────────────────

class TestIsSimpleQuestion:
    def test_planets_count_ru(self):
        assert is_simple_question("Сколько планет в солнечной системе?") is True

    def test_capital_france(self):
        assert is_simple_question("Столица Франции") is True

    def test_capital_france_question(self):
        assert is_simple_question("Какая столица Франции?") is True

    def test_who_is_ru(self):
        assert is_simple_question("Кто такой Пушкин?") is True

    def test_what_is_en(self):
        assert is_simple_question("What is photosynthesis?") is True

    def test_how_many_en(self):
        assert is_simple_question("How many countries are in the EU?") is True

    def test_where_is_en(self):
        assert is_simple_question("Where is Tokyo?") is True

    def test_when_was_en(self):
        assert is_simple_question("When was World War 2?") is True

    def test_how_much_en(self):
        assert is_simple_question("How much does gold weigh per gram?") is True

    def test_year_query(self):
        assert is_simple_question("Год основания Москвы?") is True

    def test_weight_query(self):
        assert is_simple_question("Вес слона в тоннах?") is True

    def test_compare_not_simple(self):
        assert is_simple_question("Сравни Python и Java") is False

    def test_compare_en_not_simple(self):
        assert is_simple_question("Compare React vs Vue") is False

    def test_research_not_simple(self):
        assert is_simple_question("Найди топ 10 стартапов") is False

    def test_analyze_not_simple(self):
        assert is_simple_question("Анализ рынка криптовалют") is False

    def test_long_query_not_simple(self):
        long = "a" * 121
        assert is_simple_question(long) is False

    def test_pros_cons_not_simple(self):
        assert is_simple_question("Плюсы и минусы Python") is False

    def test_pros_cons_en_not_simple(self):
        assert is_simple_question("Pros and cons of React") is False

    def test_research_marker_not_simple(self):
        assert is_simple_question("Исследуй рынок AI в 2025") is False

    def test_empty_not_simple(self):
        assert is_simple_question("") is False

    def test_short_without_trigger_not_simple(self):
        assert is_simple_question("Сделай красиво") is False


# ─── quick_answer ─────────────────────────────────────────────────────────────

def _make_anthropic_mock(text="Answer."):
    """Inject a fake 'anthropic' module into sys.modules so quick_answer can import it."""
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=text)]

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    mock_anthropic_mod = types.ModuleType("anthropic")
    mock_anthropic_mod.Anthropic = MagicMock(return_value=mock_client)

    return mock_anthropic_mod, mock_client


class TestQuickAnswer:
    def test_returns_text_on_success(self):
        mock_mod, mock_client = _make_anthropic_mock("8 планет в Солнечной системе.")
        with patch.dict(sys.modules, {"anthropic": mock_mod}):
            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                # Reload to pick up patched env and module
                import importlib
                import app.services.quick_answer as qa_mod
                importlib.reload(qa_mod)
                result = qa_mod.quick_answer("Сколько планет?")
        assert result == "8 планет в Солнечной системе."

    def test_returns_none_without_api_key(self):
        saved = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            result = quick_answer("Сколько планет?")
        finally:
            if saved:
                os.environ["ANTHROPIC_API_KEY"] = saved
        assert result is None

    def test_returns_none_on_api_error(self):
        mock_mod = types.ModuleType("anthropic")
        mock_mod.Anthropic = MagicMock(side_effect=Exception("API down"))
        with patch.dict(sys.modules, {"anthropic": mock_mod}):
            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                import importlib
                import app.services.quick_answer as qa_mod
                importlib.reload(qa_mod)
                result = qa_mod.quick_answer("Сколько планет?")
        assert result is None

    def test_uses_haiku_model(self):
        mock_mod, mock_client = _make_anthropic_mock("Paris.")
        with patch.dict(sys.modules, {"anthropic": mock_mod}):
            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                import importlib
                import app.services.quick_answer as qa_mod
                importlib.reload(qa_mod)
                qa_mod.quick_answer("Capital of France?")

        call_kwargs = mock_client.messages.create.call_args
        assert "haiku" in str(call_kwargs)

    def test_strips_whitespace(self):
        mock_mod, mock_client = _make_anthropic_mock("  Paris.  ")
        with patch.dict(sys.modules, {"anthropic": mock_mod}):
            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                import importlib
                import app.services.quick_answer as qa_mod
                importlib.reload(qa_mod)
                result = qa_mod.quick_answer("Capital of France?")
        assert result == "Paris."

    def test_max_tokens_at_least_200(self):
        mock_mod, mock_client = _make_anthropic_mock("Answer.")
        with patch.dict(sys.modules, {"anthropic": mock_mod}):
            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
                import importlib
                import app.services.quick_answer as qa_mod
                importlib.reload(qa_mod)
                qa_mod.quick_answer("Test?")

        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs.get("max_tokens") >= 200


# ─── classify_message integration ─────────────────────────────────────────────

class TestClassifySimpleQuestion:
    """Integration: classify_message routes simple questions to simple_question intent."""

    def _classify(self, text: str):
        import importlib, sys
        mod_name = "tools.jarvis_smart_telegram_control"
        # Reload fresh module to avoid cached patches
        if mod_name in sys.modules:
            mod = sys.modules[mod_name]
        else:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                mod_name,
                ROOT / "tools" / "jarvis_smart_telegram_control.py",
            )
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)
        return mod.classify_message(text, {})

    def test_planets_classified_as_simple(self):
        result = self._classify("Сколько планет в солнечной системе?")
        assert result["intent"] == "simple_question"

    def test_capital_classified_as_simple(self):
        result = self._classify("Столица Франции")
        assert result["intent"] == "simple_question"

    def test_compare_not_simple(self):
        result = self._classify("Сравни Python и Java")
        # Should NOT be simple_question — should go to brain or research
        assert result["intent"] != "simple_question"


# ─── Image Gen UX Fix ──────────────────────────────────────────────────────────

class TestImageGenUXFix:
    """Tests for single-message image generation flow."""

    def _get_bot_module(self):
        import importlib, sys
        mod_name = "tools.jarvis_smart_telegram_control"
        if mod_name in sys.modules:
            return sys.modules[mod_name]
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            mod_name, ROOT / "tools" / "jarvis_smart_telegram_control.py"
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        return mod

    def test_single_send_and_get_id_before_api_call(self):
        mod = self._get_bot_module()
        calls = []

        def fake_send_and_get_id(chat_id, text):
            calls.append(("send_and_get_id", text))
            return 999

        def fake_edit_message(chat_id, msg_id, text):
            calls.append(("edit_message", text))

        def fake_backend_post(path, payload, timeout=30):
            return {"job_id": "job_123", "status": "queued"}

        with patch.object(mod, "send_and_get_id", fake_send_and_get_id), \
             patch.object(mod, "edit_message", fake_edit_message), \
             patch.object(mod, "backend_post", fake_backend_post):
            mod.run_intent(111, {"intent": "generate", "query": "нарисуй кота"}, {})

        send_calls = [c for c in calls if c[0] == "send_and_get_id"]
        edit_calls = [c for c in calls if c[0] == "edit_message"]

        assert len(send_calls) == 1, "Should send exactly one loading message"
        assert len(edit_calls) == 1, "Should edit to final result"

    def test_loading_message_text(self):
        mod = self._get_bot_module()
        sent = []

        with patch.object(mod, "send_and_get_id", lambda cid, t: sent.append(t) or 1), \
             patch.object(mod, "edit_message", lambda *a: None), \
             patch.object(mod, "backend_post", lambda *a, **kw: {"job_id": "j1", "status": "queued"}):
            mod.run_intent(111, {"intent": "generate", "query": "нарисуй кота"}, {})

        assert sent, "Loading message should be sent"
        assert "🎨" in sent[0]

    def test_edit_contains_success_indicator(self):
        """Phase 38: new flow returns URLs directly (no job_id), edit shows success."""
        mod = self._get_bot_module()
        edits = []

        with patch.object(mod, "send_and_get_id", lambda cid, t: 42), \
             patch.object(mod, "edit_message", lambda cid, mid, t: edits.append(t)), \
             patch.object(mod, "_send_photo_url", lambda *a, **kw: None), \
             patch.object(mod, "backend_post", lambda *a, **kw: {
                 "urls": ["https://img.com/1.jpg"], "provider": "replicate", "status": "ok"
             }):
            mod.run_intent(111, {"intent": "generate", "query": "нарисуй кота"}, {})

        assert edits, "edit_message should be called"
        assert "✅" in edits[0]

    def test_error_path_uses_edit_not_send(self):
        mod = self._get_bot_module()
        sends = []
        edits = []

        with patch.object(mod, "send_and_get_id", lambda cid, t: sends.append(t) or 77), \
             patch.object(mod, "edit_message", lambda cid, mid, t: edits.append(t)), \
             patch.object(mod, "backend_post", lambda *a, **kw: {"_error": "timeout"}):
            mod.run_intent(111, {"intent": "generate", "query": "нарисуй кота"}, {})

        assert len(sends) == 1, "Only loading message via send_and_get_id"
        assert edits, "Error shown via edit_message"
        assert "❌" in edits[0]

    def test_no_double_send(self):
        """There must be exactly 1 send_and_get_id call total — not 2."""
        mod = self._get_bot_module()
        count = [0]

        def fake_sagi(cid, t):
            count[0] += 1
            return 1

        with patch.object(mod, "send_and_get_id", fake_sagi), \
             patch.object(mod, "edit_message", lambda *a: None), \
             patch.object(mod, "backend_post", lambda *a, **kw: {"job_id": "j", "status": "ok"}):
            mod.run_intent(111, {"intent": "generate", "query": "нарисуй кота"}, {})

        assert count[0] == 1, f"Expected 1 send_and_get_id call, got {count[0]}"
