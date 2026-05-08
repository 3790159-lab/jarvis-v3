from pathlib import Path

from app.agent_bootstrap import ensure_required_agents


def test_ensure_required_agents_creates_missing_agents(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)

    result = ensure_required_agents(state)

    assert result["status"] == "ok"
    assert "planner_agent" in result["enabled_agents"]
    assert "executor_agent" in result["enabled_agents"]
    assert "critic_agent" in result["enabled_agents"]
    assert "shell_agent" in result["enabled_agents"]

    agents_file = state / "agents.json"
    text = agents_file.read_text(encoding="utf-8")
    assert "critic_agent" in text
    assert "planner_agent" in text


def test_ensure_required_agents_upgrades_disabled_agent(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)

    (state / "agents.json").write_text(
        """
        {
          "agents": [
            {
              "name": "critic_agent",
              "role": "Critic",
              "enabled": false,
              "capabilities": []
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    result = ensure_required_agents(state)
    assert "critic_agent" in result["enabled_agents"]

    text = (state / "agents.json").read_text(encoding="utf-8")
    assert '"enabled": true' in text
