from __future__ import annotations

"""Phase 7.5: Table quality improvement tests.

Tests for:
1. _parse_table_json — parses LLM JSON output (with/without markdown fences)
2. _llm_extract_table — calls LLM with correct prompt, returns structured data
3. build_internet_table — uses smart extraction when LLM available, falls back gracefully
"""

import json
import os
import sys
from typing import Any, Dict, List, Optional
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.jarvis_telegram_file_tools import (
    _parse_table_json,
    _llm_extract_table,
)


# ---------------------------------------------------------------------------
# _parse_table_json
# ---------------------------------------------------------------------------

def test_parse_table_json_clean():
    raw = json.dumps({"columns": ["A", "B"], "rows": [["1", "2"], ["3", "4"]]})
    result = _parse_table_json(raw)
    assert result is not None
    assert result["columns"] == ["A", "B"]
    assert len(result["rows"]) == 2


def test_parse_table_json_with_markdown_fence():
    raw = '```json\n{"columns": ["Service", "Price"], "rows": [["OpenAI", "$20/mo"]]}\n```'
    result = _parse_table_json(raw)
    assert result is not None
    assert result["columns"] == ["Service", "Price"]
    assert result["rows"][0][0] == "OpenAI"


def test_parse_table_json_with_plain_fence():
    raw = '```\n{"columns": ["X"], "rows": [["v"]]}\n```'
    result = _parse_table_json(raw)
    assert result is not None
    assert result["columns"] == ["X"]


def test_parse_table_json_invalid_returns_none():
    assert _parse_table_json("not json at all") is None
    assert _parse_table_json("{}") is None
    assert _parse_table_json('{"columns": [], "rows": []}') is None


def test_parse_table_json_missing_columns_returns_none():
    raw = json.dumps({"rows": [["a", "b"]]})
    assert _parse_table_json(raw) is None


def test_parse_table_json_missing_rows_returns_none():
    raw = json.dumps({"columns": ["A", "B"]})
    assert _parse_table_json(raw) is None


def test_parse_table_json_empty_string_returns_none():
    assert _parse_table_json("") is None


def test_parse_table_json_real_ai_services_example():
    raw = json.dumps({
        "columns": ["Service", "Category", "Pricing", "Free Tier", "API", "Strengths"],
        "rows": [
            ["OpenAI", "LLM", "$20/mo", "Yes", "Yes", "GPT-4, widely used"],
            ["Anthropic", "LLM", "$20/mo", "No", "Yes", "Safety-focused Claude"],
        ]
    })
    result = _parse_table_json(raw)
    assert result is not None
    assert "Service" in result["columns"]
    assert "Category" in result["columns"]
    assert len(result["rows"]) == 2


# ---------------------------------------------------------------------------
# _llm_extract_table — mock LLM calls
# ---------------------------------------------------------------------------

MOCK_SEARCH_RESULTS = [
    {"title": "OpenAI ChatGPT", "url": "https://openai.com", "content": "Best LLM platform"},
    {"title": "Anthropic Claude", "url": "https://anthropic.com", "content": "Safety-focused AI"},
    {"title": "Google Gemini", "url": "https://gemini.google.com", "content": "Multimodal AI"},
]

MOCK_RESEARCH_ANSWER = "Top AI services include OpenAI, Anthropic, and Google with various pricing tiers."

MOCK_LLM_RESPONSE = json.dumps({
    "columns": ["Service", "Category", "Pricing", "Free Tier", "API"],
    "rows": [
        ["OpenAI", "LLM", "$20/mo", "Yes", "Yes"],
        ["Anthropic", "LLM", "$20/mo", "No", "Yes"],
        ["Google", "LLM", "Free+", "Yes", "Yes"],
    ]
})


def _make_openai_response(content: str) -> Dict[str, Any]:
    return {"choices": [{"message": {"content": content}}]}


def _make_anthropic_response(content: str) -> Dict[str, Any]:
    return {"content": [{"text": content}]}


def test_llm_extract_table_uses_openai_when_key_present(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    import app.services.jarvis_telegram_file_tools as mod
    monkeypatch.setattr(mod, "_post_json", lambda url, payload, headers, timeout=30:
        _make_openai_response(MOCK_LLM_RESPONSE) if "openai" in url else {})

    result = mod._llm_extract_table("top AI services", MOCK_SEARCH_RESULTS, MOCK_RESEARCH_ANSWER)
    assert result is not None
    assert "Service" in result["columns"]
    assert len(result["rows"]) >= 2


def test_llm_extract_table_fallback_to_anthropic(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")

    import app.services.jarvis_telegram_file_tools as mod
    monkeypatch.setattr(mod, "_post_json", lambda url, payload, headers, timeout=30:
        _make_anthropic_response(MOCK_LLM_RESPONSE) if "anthropic" in url else {})

    result = mod._llm_extract_table("top AI services", MOCK_SEARCH_RESULTS, MOCK_RESEARCH_ANSWER)
    assert result is not None
    assert "Service" in result["columns"]


def test_llm_extract_table_returns_none_when_no_keys(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    import app.services.jarvis_telegram_file_tools as mod
    result = mod._llm_extract_table("top AI services", MOCK_SEARCH_RESULTS, MOCK_RESEARCH_ANSWER)
    assert result is None


def test_llm_extract_table_returns_none_on_bad_llm_json(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    import app.services.jarvis_telegram_file_tools as mod
    monkeypatch.setattr(mod, "_post_json", lambda url, payload, headers, timeout=30:
        _make_openai_response("sorry, I can't help with that"))

    result = mod._llm_extract_table("top AI services", MOCK_SEARCH_RESULTS, MOCK_RESEARCH_ANSWER)
    assert result is None


def test_llm_extract_table_handles_llm_api_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    import app.services.jarvis_telegram_file_tools as mod
    monkeypatch.setattr(mod, "_post_json", lambda url, payload, headers, timeout=30: {"error": "timeout"})

    result = mod._llm_extract_table("top AI services", MOCK_SEARCH_RESULTS, MOCK_RESEARCH_ANSWER)
    assert result is None


def test_llm_extract_table_prompt_contains_query(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    captured_payload = {}

    def capture_post(url, payload, headers, timeout=30):
        captured_payload.update(payload)
        return _make_openai_response(MOCK_LLM_RESPONSE)

    import app.services.jarvis_telegram_file_tools as mod
    monkeypatch.setattr(mod, "_post_json", capture_post)

    mod._llm_extract_table("top AI services 2024", MOCK_SEARCH_RESULTS, MOCK_RESEARCH_ANSWER)
    messages = captured_payload.get("messages", [])
    assert any("top AI services 2024" in (m.get("content") or "") for m in messages)


# ---------------------------------------------------------------------------
# build_internet_table — integration with smart extraction
# ---------------------------------------------------------------------------

def test_build_internet_table_uses_smart_columns_when_llm_works(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    import app.services.jarvis_telegram_file_tools as mod
    monkeypatch.setattr(mod, "ART_DIR", tmp_path)
    monkeypatch.setattr(mod, "internet_search", lambda q, max_results=8: {
        "data": {"results": MOCK_SEARCH_RESULTS}
    })
    monkeypatch.setattr(mod, "internet_research", lambda q: {"answer": MOCK_RESEARCH_ANSWER})
    monkeypatch.setattr(mod, "_post_json", lambda url, payload, headers, timeout=30:
        _make_openai_response(MOCK_LLM_RESPONSE))

    result = mod.build_internet_table("top AI services", send_to_telegram=False)

    assert result["ok"] is True
    assert result["used_smart_extraction"] is True
    assert "Service" in result["columns"]
    assert result["rows_count"] >= 1


def test_build_internet_table_falls_back_gracefully_when_no_llm(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    import app.services.jarvis_telegram_file_tools as mod
    monkeypatch.setattr(mod, "ART_DIR", tmp_path)
    monkeypatch.setattr(mod, "internet_search", lambda q, max_results=8: {
        "data": {"results": MOCK_SEARCH_RESULTS}
    })
    monkeypatch.setattr(mod, "internet_research", lambda q: {"answer": MOCK_RESEARCH_ANSWER})

    result = mod.build_internet_table("top AI services", send_to_telegram=False)

    assert result["ok"] is True
    assert result["used_smart_extraction"] is False
    assert result["columns"] == ["rank", "title", "url", "score", "summary"]
    assert result["rows_count"] == 3


def test_build_internet_table_smart_columns_not_rank_title(monkeypatch, tmp_path):
    """Smart extraction must NOT produce raw rank/title/url columns."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    import app.services.jarvis_telegram_file_tools as mod
    monkeypatch.setattr(mod, "ART_DIR", tmp_path)
    monkeypatch.setattr(mod, "internet_search", lambda q, max_results=8: {
        "data": {"results": MOCK_SEARCH_RESULTS}
    })
    monkeypatch.setattr(mod, "internet_research", lambda q: {"answer": MOCK_RESEARCH_ANSWER})
    monkeypatch.setattr(mod, "_post_json", lambda url, payload, headers, timeout=30:
        _make_openai_response(MOCK_LLM_RESPONSE))

    result = mod.build_internet_table("top AI services", send_to_telegram=False)

    assert result["used_smart_extraction"] is True
    cols = result["columns"]
    assert "rank" not in cols, f"Smart extraction should not use 'rank' column, got: {cols}"
    assert "url" not in cols, f"Smart extraction should not use 'url' column, got: {cols}"
