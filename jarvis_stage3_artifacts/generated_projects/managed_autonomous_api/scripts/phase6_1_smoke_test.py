from app.agents.registry import AgentRegistry
from app.services.role_router import choose_agent, build_handoff_path, validate_routing_request
from app.agents.runtime import preflight_agent_task
from app.agents.schemas import AgentTaskEnvelope

registry = AgentRegistry()
registry.seed_defaults()
registry.refresh_health()

validation = validate_routing_request(
    objective="Implement endpoint patch and validate result",
    requested_tools=["python", "filesystem"],
    task_type="code"
)

decision = choose_agent(
    objective="Implement endpoint patch and validate result",
    task_type="code",
    requested_tools=["python", "filesystem"]
)

task = AgentTaskEnvelope(
    task_id="phase6_1_smoke_001",
    mission_id="phase6_1_mission",
    objective="Implement endpoint patch and validate result",
    requested_tools=["python", "filesystem"]
)

result = preflight_agent_task(decision["agent_id"], task)

print("PHASE6_1_SMOKE_OK")
print({
    "validation": validation,
    "decision": decision,
    "handoff_path": build_handoff_path(decision["agent_id"]),
    "preflight_status": result.status
})
