from app.services.bootstrap_agent import BootstrapAgentService
from app.agents.registry import AgentRegistry

service = BootstrapAgentService()
check = service.self_check()

created = service.create_from_template(
    template_name="research_agent",
    agent_id="research_core_phase9",
    name="Research Core Phase 9"
)

registry = AgentRegistry()
agent = registry.get("research_core_phase9")

print("PHASE9_SMOKE_OK")
print({
    "self_check": check["status"],
    "created_status": created["status"],
    "agent_exists": agent is not None,
    "agent_role": None if agent is None else agent.role
})
