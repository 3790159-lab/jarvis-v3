from app.services.bootstrap_agent import BootstrapAgentService
from app.agents.registry import AgentRegistry
from app.agents.policies import load_policy_profiles


def test_policy_profiles_load_with_bom_safe_reader():
    profiles = load_policy_profiles()
    assert "safe" in profiles
    assert "dev" in profiles
    assert "admin" in profiles


def test_bootstrap_self_check():
    service = BootstrapAgentService()
    result = service.self_check()
    assert result["status"] == "ok"


def test_bootstrap_create_research_agent():
    service = BootstrapAgentService()
    result = service.create_from_template(
        template_name="research_agent",
        agent_id="research_core",
        name="Research Core"
    )
    assert result["status"] == "ok"
    assert result["post_check"]["agent_exists"] is True

    registry = AgentRegistry()
    agent = registry.get("research_core")
    assert agent is not None
    assert agent.role == "research"


def test_bootstrap_rejects_empty_agent_id():
    service = BootstrapAgentService()
    result = service.create_from_template(
        template_name="research_agent",
        agent_id="",
        name="Bad Agent"
    )
    assert result["status"] == "error"


def test_bootstrap_rejects_bad_agent_id_chars():
    service = BootstrapAgentService()
    result = service.create_from_template(
        template_name="research_agent",
        agent_id="bad id!",
        name="Bad Agent"
    )
    assert result["status"] == "error"
