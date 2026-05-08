"""Tests for Phase 28.0: Micro-fixes from Block D2 reality testing."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.services.n8n_integration as n8n_mod
from app.services.n8n_integration import workflow_list_text


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_workflows(n: int, all_active: bool = True) -> list:
    return [
        {"id": str(i), "name": f"Workflow {i}", "active": all_active or (i % 2 == 0)}
        for i in range(1, n + 1)
    ]


# ─── workflow_list_text: pagination ──────────────────────────────────────────

class TestWorkflowListTextPagination:
    def test_small_list_no_pagination(self):
        wfs = _make_workflows(5)
        text = workflow_list_text(wfs, page=0, page_size=20)
        assert "Workflow 5" in text
        assert "следующая" not in text.lower()

    def test_large_list_shows_next_hint(self):
        wfs = _make_workflows(60)
        text = workflow_list_text(wfs, page=0, page_size=20)
        assert "+40 ещё" in text
        assert "/n8n list 1" in text

    def test_second_page(self):
        wfs = _make_workflows(60)
        text = workflow_list_text(wfs, page=1, page_size=20)
        assert "Workflow 21" in text
        assert "Workflow 40" in text
        assert "Workflow 1" not in text

    def test_last_page_shows_page_info(self):
        wfs = _make_workflows(45)
        text = workflow_list_text(wfs, page=2, page_size=20)
        assert "страница 3" in text
        assert "Workflow 41" in text

    def test_total_count_in_header(self):
        wfs = _make_workflows(60)
        text = workflow_list_text(wfs)
        assert "60 всего" in text

    def test_empty_list(self):
        text = workflow_list_text([])
        assert "Нет" in text

    def test_filter_matching(self):
        wfs = [
            {"id": "1", "name": "Daily Report", "active": True},
            {"id": "2", "name": "Weekly Digest", "active": True},
            {"id": "3", "name": "Daily Backup", "active": False},
        ]
        text = workflow_list_text(wfs, filter_str="daily")
        assert "Daily Report" in text
        assert "Daily Backup" in text
        assert "Weekly Digest" not in text
        assert "2 найдено" in text

    def test_filter_no_match(self):
        wfs = _make_workflows(3)
        text = workflow_list_text(wfs, filter_str="nonexistent")
        assert "Нет" in text

    def test_filter_hint_in_small_list(self):
        wfs = _make_workflows(5)
        text = workflow_list_text(wfs)
        assert "/n8n list active" in text or "Фильтр" in text

    def test_page_size_respected(self):
        wfs = _make_workflows(10)
        text = workflow_list_text(wfs, page=0, page_size=3)
        assert "Workflow 1" in text
        assert "Workflow 3" in text
        assert "Workflow 4" not in text


# ─── quick_answer: capabilities in system prompt ─────────────────────────────

class TestQuickAnswerSystemPrompt:
    """Verify the system prompt includes capability info."""

    def test_capabilities_mentioned_in_system_prompt(self):
        """Mock Anthropic call and verify system prompt has capabilities."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Да, я могу генерировать изображения.")]
        mock_client.messages.create.return_value = mock_response

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("anthropic.Anthropic", return_value=mock_client):
                from app.services.quick_answer import quick_answer
                result = quick_answer("А ты можешь генерировать фото?")

        assert result == "Да, я могу генерировать изображения."
        call_kwargs = mock_client.messages.create.call_args[1]
        system = call_kwargs["system"]
        assert "Generate images" in system or "изображени" in system.lower()

    def test_max_tokens_increased(self):
        """300 tokens for capability answers, not 200."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Jarvis ответил.")]
        mock_client.messages.create.return_value = mock_response

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("anthropic.Anthropic", return_value=mock_client):
                from app.services import quick_answer as qa_mod
                # reload to pick up changes
                import importlib
                importlib.reload(qa_mod)
                qa_mod.quick_answer("Что ты умеешь?")

        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["max_tokens"] >= 300


# ─── /job without argument ───────────────────────────────────────────────────

class TestJobCommandEmptyArgument:
    """Test that /job without argument sends hint, not 404."""

    def test_job_hint_message_content(self):
        """The hint message must contain usage and example."""
        import tools.jarvis_smart_telegram_control as bot_mod

        sent_messages = []

        def fake_send(chat_id, text, reply_markup=None):
            sent_messages.append(text)

        with patch.object(bot_mod, "send", side_effect=fake_send):
            with patch.object(bot_mod, "backend_get") as mock_get:
                # Simulate /job with empty query
                bot_mod.handle_command("123", "/job", "", {})

        assert sent_messages, "No message sent"
        hint = sent_messages[0]
        assert "/job <job_id>" in hint or "job_id" in hint.lower()
        assert "Пример" in hint or "/gen" in hint
        # backend_get should NOT have been called with empty id
        mock_get.assert_not_called()

    def test_job_with_id_calls_backend(self):
        """With a real job_id, backend_get IS called."""
        import tools.jarvis_smart_telegram_control as bot_mod

        sent_messages = []

        def fake_send(chat_id, text, reply_markup=None):
            sent_messages.append(text)

        fake_data = {"status": "done", "result": {"run_id": "r1", "image_urls": [], "drive_folder_url": ""}}

        with patch.object(bot_mod, "send", side_effect=fake_send):
            with patch.object(bot_mod, "backend_get", return_value=fake_data) as mock_get:
                bot_mod.handle_command("123", "/job", "abc123", {})

        mock_get.assert_called_once()
        assert sent_messages
