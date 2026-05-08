from pathlib import Path

from app.agent_bootstrap import ensure_required_agents


def test_required_agents_are_present(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)

    result = ensure_required_agents(state)

    assert result["status"] == "ok"
    assert "planner_agent" in result["enabled_agents"]
    assert "executor_agent" in result["enabled_agents"]
    assert "critic_agent" in result["enabled_agents"]
    assert "shell_agent" in result["enabled_agents"]
