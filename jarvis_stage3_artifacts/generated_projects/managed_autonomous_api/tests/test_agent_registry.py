from app.agents.registry import AgentRegistry
from app.agents.runtime import preflight_agent_task
from app.agents.schemas import AgentTaskEnvelope


def test_registry_seed_defaults():
    registry = AgentRegistry()
    agents = registry.seed_defaults()
    assert len(agents) >= 5
    assert registry.get("supervisor_core") is not None
    assert registry.get("planner_core") is not None
    assert registry.get("executor_core") is not None
    assert registry.get("qa_core") is not None
    assert registry.get("code_core") is not None


def test_executor_preflight_allows_whitelisted_tools():
    registry = AgentRegistry()
    registry.seed_defaults()

    task = AgentTaskEnvelope(
        task_id="task_ok_001",
        objective="Run allowed tools",
        requested_tools=["shell", "python"]
    )

    result = preflight_agent_task("executor_core", task)
    assert result.status == "completed"
    assert result.next_action == "execution_allowed"


def test_executor_preflight_blocks_disallowed_tools():
    registry = AgentRegistry()
    registry.seed_defaults()

    task = AgentTaskEnvelope(
        task_id="task_block_001",
        objective="Try forbidden tool",
        requested_tools=["registry"]
    )

    result = preflight_agent_task("executor_core", task)
    assert result.status == "blocked"
    assert result.requires_human is True


def test_planner_policy_blocks_shell():
    registry = AgentRegistry()
    registry.seed_defaults()

    task = AgentTaskEnvelope(
        task_id="task_plan_001",
        objective="Try to use shell from planner",
        requested_tools=["shell"]
    )

    result = preflight_agent_task("planner_core", task)
    assert result.status == "blocked"


def test_refresh_health_marks_active_agents_healthy():
    registry = AgentRegistry()
    registry.seed_defaults()
    report = registry.refresh_health()
    assert report["updated_count"] >= 5
    agent = registry.get("supervisor_core")
    assert agent is not None
    assert agent.health == "healthy"
