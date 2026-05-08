from app.agents.registry import AgentRegistry
from app.services.role_router import choose_agent, build_handoff_path
from app.agents.runtime import preflight_agent_task
from app.agents.schemas import AgentTaskEnvelope

registry = AgentRegistry()
registry.seed_defaults()
registry.refresh_health()

decision = choose_agent(
    objective="Implement endpoint patch and validate result",
    task_type="code",
    requested_tools=["python", "filesystem"]
)

task = AgentTaskEnvelope(
    task_id="phase6_smoke_001",
    mission_id="phase6_mission",
    objective="Implement endpoint patch and validate result",
    requested_tools=["python", "filesystem"]
)

result = preflight_agent_task(decision["agent_id"], task)

print("PHASE6_SMOKE_OK")
print({
    "decision": decision,
    "handoff_path": build_handoff_path(decision["agent_id"]),
    "preflight_status": result.status
})
