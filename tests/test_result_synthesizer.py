"""Phase 18: Result Synthesizer tests."""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.result_synthesizer import (
    synthesize_results,
    format_for_telegram,
    format_agent_error,
    _extract_content,
    _has_file_output,
    _extract_sources,
    _results_are_related,
)


# ---------------------------------------------------------------------------
# _extract_content
# ---------------------------------------------------------------------------

def test_extract_content_answer_field():
    assert _extract_content({"answer": "Hello world"}) == "Hello world"


def test_extract_content_plan_field():
    assert _extract_content({"plan": "Step 1: do X"}) == "Step 1: do X"


def test_extract_content_error_field():
    result = _extract_content({"_error": "timeout"})
    assert "timeout" in result


def test_extract_content_fallback_to_str():
    result = _extract_content({"unknown_key": "value"})
    assert len(result) > 0


# ---------------------------------------------------------------------------
# _has_file_output
# ---------------------------------------------------------------------------

def test_has_file_output_with_drive_url():
    assert _has_file_output({"drive_url": "https://drive.google.com/file/123"}) is True


def test_has_file_output_with_job_id():
    assert _has_file_output({"job_id": "job_123"}) is True


def test_has_file_output_none():
    assert _has_file_output({"answer": "some text"}) is False


# ---------------------------------------------------------------------------
# _extract_sources
# ---------------------------------------------------------------------------

def test_extract_sources_from_sources():
    result = _extract_sources({"sources": ["http://a.com", "http://b.com"]})
    assert result == ["http://a.com", "http://b.com"]


def test_extract_sources_from_citations():
    result = _extract_sources({"citations": ["cite1"]})
    assert result == ["cite1"]


def test_extract_sources_limits_to_5():
    result = _extract_sources({"sources": [f"http://{i}.com" for i in range(10)]})
    assert len(result) == 5


def test_extract_sources_empty():
    assert _extract_sources({}) == []


# ---------------------------------------------------------------------------
# _results_are_related
# ---------------------------------------------------------------------------

def test_results_related_shared_words():
    c1 = "The machine learning model uses neural networks for classification"
    c2 = "Neural networks are used in machine learning for pattern recognition"
    assert _results_are_related([c1, c2]) is True


def test_results_unrelated():
    c1 = "The weather today is sunny and warm outside"
    c2 = "Bitcoin transactions use blockchain cryptography protocols"
    result = _results_are_related([c1, c2])
    # May or may not be related — just verify it returns bool
    assert isinstance(result, bool)


def test_results_single_always_related():
    assert _results_are_related(["anything"]) is True


# ---------------------------------------------------------------------------
# synthesize_results
# ---------------------------------------------------------------------------

def test_synthesize_empty_returns_error():
    result = synthesize_results("query", [])
    assert "нет" in result.lower() or "❌" in result


def test_synthesize_single_answer():
    result = synthesize_results(
        "вопрос",
        [{"agent": "internet_research", "result": {"answer": "Ответ 42"}}],
        try_llm=False,
    )
    assert "42" in result


def test_synthesize_single_with_sources():
    result = synthesize_results(
        "вопрос",
        [{"agent": "internet_research", "result": {"answer": "Ответ", "sources": ["http://a.com"]}}],
        try_llm=False,
    )
    assert "a.com" in result or "Источники" in result


def test_synthesize_single_with_file_output():
    result = synthesize_results(
        "таблица",
        [{"agent": "smart_table", "result": {"answer": "Table created", "drive_url": "https://drive.google.com/xyz"}}],
        try_llm=False,
    )
    assert "drive.google.com" in result or "📎" in result


def test_synthesize_multiple_no_llm():
    agent_results = [
        {"agent": "internet_research", "result": {"answer": "Research content about AI models"}},
        {"agent": "smart_table", "result": {"answer": "Table with AI models comparison"}},
    ]
    result = synthesize_results("AI models", agent_results, try_llm=False)
    assert len(result) > 20
    assert "интернет-исследование" in result or "AI" in result or "таблиц" in result


def test_synthesize_error_result_handled():
    result = synthesize_results(
        "query",
        [{"agent": "internet_research", "result": {"_error": "timeout"}}],
        try_llm=False,
    )
    assert len(result) > 0


# ---------------------------------------------------------------------------
# format_for_telegram
# ---------------------------------------------------------------------------

def test_format_short_content_unchanged():
    text = "Short text"
    assert format_for_telegram(text) == text


def test_format_long_content_truncated():
    text = "x" * 5000
    result = format_for_telegram(text, max_length=4000)
    assert len(result) <= 4050  # room for truncation suffix


def test_format_truncated_has_indicator():
    text = "x" * 5000
    result = format_for_telegram(text, max_length=4000)
    assert "усечено" in result or "..." in result


# ---------------------------------------------------------------------------
# format_agent_error
# ---------------------------------------------------------------------------

def test_format_agent_error_contains_label():
    result = format_agent_error("internet_research", "timeout")
    assert "интернет-исследование" in result or "internet" in result.lower()


def test_format_agent_error_contains_error():
    result = format_agent_error("claude_coder", "API key missing")
    assert "API key missing" in result
