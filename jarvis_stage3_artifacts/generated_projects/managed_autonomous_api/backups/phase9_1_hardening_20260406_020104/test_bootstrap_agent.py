from app.services.bootstrap_agent import BootstrapAgentService
from app.agents.registry import AgentRegistry


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
