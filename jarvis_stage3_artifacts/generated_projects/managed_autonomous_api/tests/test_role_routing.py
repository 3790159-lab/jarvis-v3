from app.agents.registry import AgentRegistry
from app.services.role_router import choose_agent, build_handoff_path, build_handoff_id, validate_routing_request
from app.agents.runtime import preflight_agent_task
from app.agents.schemas import AgentTaskEnvelope


def setup_module():
    registry = AgentRegistry()
    registry.seed_defaults()
    registry.refresh_health()


def test_plan_task_routes_to_planner():
    decision = choose_agent(
        objective="Build a strategy and plan for new mission",
        task_type="plan",
        requested_tools=[]
    )
    assert decision["agent_id"] == "planner_core"


def test_code_task_routes_to_code():
    decision = choose_agent(
        objective="Implement API patch and update script",
        task_type="code",
        requested_tools=["python", "filesystem"]
    )
    assert decision["agent_id"] == "code_core"


def test_executor_task_routes_to_executor():
    decision = choose_agent(
        objective="Run HTTP request and shell execution",
        task_type="execute",
        requested_tools=["shell", "http"]
    )
    assert decision["agent_id"] == "executor_core"


def test_handoff_for_code_includes_planner_and_qa():
    path = build_handoff_path("code_core")
    assert path == ["planner_core", "code_core", "qa_core"]


def test_routed_preflight_for_executor_passes():
    task = AgentTaskEnvelope(
        task_id="phase6_exec_001",
        objective="Run execution task",
        requested_tools=["shell"]
    )
    result = preflight_agent_task("executor_core", task)
    assert result.status == "completed"


def test_empty_objective_fails_validation():
    report = validate_routing_request("", [], None)
    assert report["ok"] is False
    assert "objective must not be empty" in report["errors"]


def test_handoff_id_is_stable():
    a = build_handoff_id("task_1", "code_core")
    b = build_handoff_id("task_1", "code_core")
    assert a == b
    assert len(a) == 16
