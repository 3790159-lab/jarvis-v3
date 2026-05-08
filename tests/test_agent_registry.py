"""Phase 15: Agent Registry tests."""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.agent_registry import (
    AGENTS,
    get_agent,
    list_agents_by_capability,
    list_available_agents,
    estimate_cost,
    check_all_agents_health,
    CAPABILITY_REGISTRY,
)


# ---------------------------------------------------------------------------
# AGENTS structure
# ---------------------------------------------------------------------------

def test_agents_has_required_keys():
    required = {"type", "capabilities", "cost_tier", "speed_tier", "available", "label"}
    for aid, cfg in AGENTS.items():
        missing = required - set(cfg.keys())
        assert not missing, f"Agent '{aid}' missing keys: {missing}"


def test_agents_contains_core_agents():
    core = [
        "internet_research", "smart_table", "claude_coder",
        "openai_reasoner", "perplexity_researcher", "file_processor",
        "ai_engineer", "image_generator", "n8n_workflow",
    ]
    for aid in core:
        assert aid in AGENTS, f"Missing agent: {aid}"


def test_agents_count_ten_or_more():
    assert len(AGENTS) >= 10


def test_cowork_agent_in_registry():
    # cowork_file_agent is now enabled in Block D1 (available=True)
    assert "cowork_file_agent" in AGENTS


def test_cowork_agent_is_available():
    # enabled in Block D1
    assert AGENTS["cowork_file_agent"]["available"] is True


def test_available_agents_includes_cowork():
    available = list_available_agents()
    assert "cowork_file_agent" in available


def test_all_http_agents_have_endpoint():
    # api_tool agents that use a direct HTTP endpoint must declare it
    # (some api_tool agents like google_drive use SDK instead)
    sdk_agents = {"google_drive"}
    for aid, cfg in AGENTS.items():
        if cfg.get("type") == "api_tool" and cfg.get("available") and aid not in sdk_agents:
            assert "endpoint" in cfg, f"API agent '{aid}' missing endpoint"


def test_capabilities_are_lists():
    for aid, cfg in AGENTS.items():
        assert isinstance(cfg.get("capabilities"), list), f"Agent '{aid}' capabilities must be list"


# ---------------------------------------------------------------------------
# get_agent
# ---------------------------------------------------------------------------

def test_get_agent_returns_config():
    cfg = get_agent("internet_research")
    assert cfg is not None
    assert cfg["type"] == "api_tool"


def test_get_agent_unknown_returns_none():
    assert get_agent("nonexistent_agent_xyz") is None


def test_get_agent_cowork_returns_config():
    cfg = get_agent("cowork_file_agent")
    assert cfg is not None
    assert cfg["available"] is True  # enabled in Block D1


# ---------------------------------------------------------------------------
# list_agents_by_capability
# ---------------------------------------------------------------------------

def test_list_by_capability_web_search():
    agents = list_agents_by_capability("web_search")
    assert "internet_research" in agents


def test_list_by_capability_excel():
    agents = list_agents_by_capability("excel")
    assert "smart_table" in agents


def test_list_by_capability_code_generation():
    agents = list_agents_by_capability("code_generation")
    assert "claude_coder" in agents


def test_list_by_capability_includes_cowork():
    # cowork_file_agent is now available and has file_organization
    agents = list_agents_by_capability("file_organization")
    assert "cowork_file_agent" in agents


def test_list_by_capability_no_match_returns_empty():
    agents = list_agents_by_capability("quantum_teleportation_xyz")
    assert agents == []


def test_list_by_capability_video():
    agents = list_agents_by_capability("video_generation")
    assert "video_generator" in agents


# ---------------------------------------------------------------------------
# estimate_cost
# ---------------------------------------------------------------------------

def test_estimate_cost_single_high():
    cost = estimate_cost(["claude_coder"])
    assert cost == "high"


def test_estimate_cost_single_low():
    cost = estimate_cost(["openai_reasoner"])
    assert cost == "low"


def test_estimate_cost_mixed_returns_max():
    cost = estimate_cost(["openai_reasoner", "claude_coder"])
    assert cost == "high"


def test_estimate_cost_empty_plan():
    cost = estimate_cost([])
    assert cost in ("free", "low", "medium", "high")


def test_estimate_cost_free_agent():
    cost = estimate_cost(["obsidian_writer"])
    assert cost == "free"


# ---------------------------------------------------------------------------
# check_all_agents_health — mock HTTP
# ---------------------------------------------------------------------------

def test_check_health_cowork_is_true(monkeypatch):
    # cowork_file_agent is available=True and has no health_check → assumed True
    result = check_all_agents_health()
    assert result.get("cowork_file_agent") is True


def test_check_health_no_health_check_assumes_true():
    # claude_coder has no health_check → assumed True
    result = check_all_agents_health()
    assert result.get("claude_coder") is True


def test_check_health_returns_dict_for_all_agents():
    result = check_all_agents_health()
    assert set(result.keys()) == set(AGENTS.keys())


# ---------------------------------------------------------------------------
# CAPABILITY_REGISTRY legacy view
# ---------------------------------------------------------------------------

def test_capability_registry_is_list():
    assert isinstance(CAPABILITY_REGISTRY, list)


def test_capability_registry_has_required_fields():
    for cap in CAPABILITY_REGISTRY:
        assert "id" in cap
        assert "label" in cap
        assert "status" in cap


def test_capability_registry_includes_cowork():
    # cowork_file_agent is now available, should appear in registry
    ids = [c["id"] for c in CAPABILITY_REGISTRY]
    assert "cowork_file_agent" in ids


def test_capability_registry_includes_internet_research():
    ids = [c["id"] for c in CAPABILITY_REGISTRY]
    assert "internet_research" in ids
