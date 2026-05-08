from app.agents.registry import AgentRegistry
from app.agents.runtime import preflight_agent_task
from app.agents.schemas import AgentTaskEnvelope

registry = AgentRegistry()
agents = registry.seed_defaults()

task = AgentTaskEnvelope(
    task_id="smoke_phase5_001",
    mission_id="mission_phase5",
    objective="Validate agent control plane",
    requested_tools=["shell", "python"]
)

result = preflight_agent_task("executor_core", task)

print("PHASE5_SMOKE_OK")
print({
    "agents_count": len(agents),
    "result_status": result.status,
    "next_action": result.next_action,
    "agent_id": result.agent_id
})
