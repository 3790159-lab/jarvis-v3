"""Phase 17: Agent Health Mesh tests."""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


from app.services.agent_health_mesh import (
    check_agent_health,
    check_all_agents,
    get_healthy_agent_for_capability,
    select_agents_with_fallback,
    agents_status_text,
    start_agent_health_monitor,
    stop_agent_health_monitor,
)


# ---------------------------------------------------------------------------
# check_agent_health
# ---------------------------------------------------------------------------

def test_cowork_agent_health_is_bool():
    # cowork_file_agent is now available in Block D1; health = bool
    result = check_agent_health("cowork_file_agent")
    assert isinstance(result, bool)


def test_unknown_agent_is_false():
    result = check_agent_health("nonexistent_xyz_123")
    assert result is False


def test_llm_agent_with_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
    result = check_agent_health("claude_coder", use_cache=False)
    assert result is True


def test_llm_agent_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = check_agent_health("claude_coder", use_cache=False)
    assert result is False


def test_openai_agent_with_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-456")
    result = check_agent_health("openai_reasoner", use_cache=False)
    assert result is True


def test_check_all_agents_returns_dict():
    result = check_all_agents(use_cache=True)
    assert isinstance(result, dict)


def test_check_all_agents_has_all_keys():
    from app.services.agent_registry import AGENTS
    result = check_all_agents(use_cache=True)
    assert set(result.keys()) == set(AGENTS.keys())


def test_check_all_agents_cowork_in_result():
    # cowork_file_agent is available; check it's in the health dict
    result = check_all_agents(use_cache=True)
    assert "cowork_file_agent" in result
    assert isinstance(result["cowork_file_agent"], bool)


# ---------------------------------------------------------------------------
# get_healthy_agent_for_capability
# ---------------------------------------------------------------------------

def test_get_healthy_agent_all_down(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    # May return None or an http-based agent depending on local backend
    result = get_healthy_agent_for_capability("code_generation", use_cache=False)
    # result could be None or an agent — just check it doesn't raise
    assert result is None or isinstance(result, str)


def test_get_healthy_agent_for_capability_with_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    result = get_healthy_agent_for_capability("code_generation", use_cache=False)
    assert result == "claude_coder"


# ---------------------------------------------------------------------------
# select_agents_with_fallback
# ---------------------------------------------------------------------------

def test_select_with_fallback_returns_dict():
    result = select_agents_with_fallback(["web_search", "excel"])
    assert isinstance(result, dict)
    assert "web_search" in result
    assert "excel" in result


def test_select_with_fallback_unknown_cap():
    result = select_agents_with_fallback(["quantum_teleportation_xyz"])
    assert result.get("quantum_teleportation_xyz") is None


# ---------------------------------------------------------------------------
# agents_status_text
# ---------------------------------------------------------------------------

def test_agents_status_text_is_str():
    text = agents_status_text(use_cache=True)
    assert isinstance(text, str)
    assert len(text) > 10


def test_agents_status_text_contains_sections():
    text = agents_status_text(use_cache=True)
    assert "LLM" in text or "🧠" in text


def test_agents_status_text_shows_count():
    text = agents_status_text(use_cache=True)
    assert "/" in text  # "X/Y агентов готовы"


# ---------------------------------------------------------------------------
# Background monitor
# ---------------------------------------------------------------------------

def test_monitor_start_stop():
    start_agent_health_monitor(interval=600)
    stop_agent_health_monitor()
    # Should not raise
