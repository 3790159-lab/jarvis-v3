from app.services.bootstrap_agent import BootstrapAgentService
from app.agents.registry import AgentRegistry

service = BootstrapAgentService()
check = service.self_check()

created = service.create_from_template(
    template_name="memory_agent",
    agent_id="memory_core_phase9_fix",
    name="Memory Core Phase 9 Fix"
)

registry = AgentRegistry()
agent = registry.get("memory_core_phase9_fix")

print("PHASE9_1_SMOKE_OK")
print({
    "self_check": check["status"],
    "created_status": created["status"],
    "agent_exists": agent is not None,
    "agent_role": None if agent is None else agent.role
})
