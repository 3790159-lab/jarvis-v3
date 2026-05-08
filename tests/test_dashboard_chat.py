"""Phase H1.4: Dashboard chat timeout fix tests."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.routers.jarvis_dashboard_router import (
    _process_query,
    _run_quick_answer,
    _run_research,
)


# ---------------------------------------------------------------------------
# _run_quick_answer
# ---------------------------------------------------------------------------

class TestRunQuickAnswer:
    def test_returns_none_for_research_query(self):
        result = _run_quick_answer("сравни iPhone и Android")
        assert result is None

    def test_calls_quick_answer_for_simple_query(self):
        with patch("app.services.quick_answer.quick_answer", return_value="42") as mock_qa, \
             patch("app.services.quick_answer.is_simple_question", return_value=True):
            result = _run_quick_answer("сколько планет?")
        assert result == "42"

    def test_returns_none_when_quick_answer_returns_none(self):
        with patch("app.services.quick_answer.quick_answer", return_value=None), \
             patch("app.services.quick_answer.is_simple_question", return_value=True):
            result = _run_quick_answer("сколько планет?")
        assert result is None

    def test_returns_none_on_import_error(self):
        with patch.dict("sys.modules", {"app.services.quick_answer": None}):
            result = _run_quick_answer("что такое python?")
        assert result is None


# ---------------------------------------------------------------------------
# _run_research
# ---------------------------------------------------------------------------

class TestRunResearch:
    def test_returns_answer_field(self):
        import urllib.request
        ctx = MagicMock()
        ctx.read.return_value = b'{"answer": "Paris"}'
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=ctx)
        cm.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=cm):
            result = _run_research("столица Франции")
        assert result == "Paris"

    def test_returns_result_field_when_no_answer(self):
        import urllib.request
        ctx = MagicMock()
        ctx.read.return_value = b'{"result": "Some result"}'
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=ctx)
        cm.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=cm):
            result = _run_research("test query")
        assert result == "Some result"

    def test_fallback_message_when_empty_response(self):
        import urllib.request
        ctx = MagicMock()
        ctx.read.return_value = b'{}'
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=ctx)
        cm.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=cm):
            result = _run_research("test")
        assert "пустой" in result


# ---------------------------------------------------------------------------
# _process_query (async)
# ---------------------------------------------------------------------------

class TestProcessQuery:
    def test_quick_answer_wins_for_simple_question(self):
        with patch("app.routers.jarvis_dashboard_router._run_quick_answer",
                   return_value="42 units") as mock_qa:
            result = asyncio.run(
                _process_query("сколько планет?")
            )
        assert result == "42 units"
        mock_qa.assert_called_once_with("сколько планет?")

    def test_falls_back_to_research_when_quick_returns_none(self):
        with patch("app.routers.jarvis_dashboard_router._run_quick_answer",
                   return_value=None), \
             patch("app.routers.jarvis_dashboard_router._run_research",
                   return_value="Research result"):
            result = asyncio.run(
                _process_query("расскажи про Python")
            )
        assert result == "Research result"

    def test_returns_timeout_message_on_slow_research(self):
        original_wait_for = asyncio.wait_for
        call_count = [0]

        async def mock_wait_for(coro, timeout):
            call_count[0] += 1
            if call_count[0] == 2:  # second call is research
                # Cancel the future before raising
                try:
                    coro.cancel()
                except Exception:
                    pass
                raise asyncio.TimeoutError()
            return await original_wait_for(coro, timeout)

        with patch("app.routers.jarvis_dashboard_router._run_quick_answer",
                   return_value=None), \
             patch("app.routers.jarvis_dashboard_router.asyncio.wait_for",
                   side_effect=mock_wait_for):
            result = asyncio.run(
                _process_query("some slow query")
            )
        assert "⏱" in result
        assert "30s" in result

    def test_returns_error_message_on_research_exception(self):
        with patch("app.routers.jarvis_dashboard_router._run_quick_answer",
                   return_value=None), \
             patch("app.routers.jarvis_dashboard_router._run_research",
                   side_effect=Exception("connection refused")):
            result = asyncio.run(
                _process_query("test")
            )
        assert "Ошибка" in result
        assert "connection refused" in result

    def test_quick_answer_timeout_falls_back_to_research(self):
        original_wait_for = asyncio.wait_for
        call_count = [0]

        async def mock_wait_for(coro, timeout):
            call_count[0] += 1
            if call_count[0] == 1:  # first call is quick_answer
                coro.close()
                raise asyncio.TimeoutError()
            return await original_wait_for(coro, timeout)

        with patch("app.routers.jarvis_dashboard_router.asyncio.wait_for",
                   side_effect=mock_wait_for), \
             patch("app.routers.jarvis_dashboard_router._run_research",
                   return_value="Research fallback"):
            result = asyncio.run(
                _process_query("slow simple question")
            )
        assert result == "Research fallback"
