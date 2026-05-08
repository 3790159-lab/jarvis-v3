from pathlib import Path

from app.runtime_repair import compute_retry_delay, repair_runtime_state


def test_compute_retry_delay_grows_and_caps():
    assert compute_retry_delay(1) == 2.0
    assert compute_retry_delay(2) == 4.0
    assert compute_retry_delay(3) == 8.0
    assert compute_retry_delay(10) == 60.0


def test_repair_removes_orphan_queue_and_resets_running(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)

    (state / "tasks.json").write_text(
        """
        {
          "tasks": [
            {
              "task_id": "task_good_1",
              "status": "running",
              "assigned_agent": "executor_agent",
              "payload": {"message": "hi"}
            },
            {
              "task_id": "task_good_2",
              "status": "queued",
              "assigned_agent": "executor_agent",
              "payload": {"message": "ok"}
            }
          ],
          "queue": ["task_missing", "task_good_1", "task_good_1", "task_good_2"]
        }
        """,
        encoding="utf-8",
    )

    (state / "agents.json").write_text(
        """
        {
          "agents": [
            {"name": "executor_agent", "enabled": true}
          ]
        }
        """,
        encoding="utf-8",
    )

    (state / "missions.json").write_text(
        """
        {
          "missions": [
            {"mission_id": "mission_1", "status": "running"}
          ]
        }
        """,
        encoding="utf-8",
    )

    result = repair_runtime_state(state)
    assert result["removed_orphan_queue_ids"] == ["task_missing"]
    assert "task_good_1" in result["reset_running_to_queued"]

    repaired = (state / "tasks.json").read_text(encoding="utf-8")
    assert '"task_missing"' not in repaired

    missions = (state / "missions.json").read_text(encoding="utf-8")
    assert '"status": "created"' in missions


def test_repair_fails_task_if_agent_missing(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)

    (state / "tasks.json").write_text(
        """
        {
          "tasks": [
            {
              "task_id": "task_a",
              "status": "queued",
              "assigned_agent": "ghost_agent",
              "payload": {}
            }
          ],
          "queue": ["task_a"]
        }
        """,
        encoding="utf-8",
    )

    (state / "agents.json").write_text(
        """
        {
          "agents": [
            {"name": "executor_agent", "enabled": true}
          ]
        }
        """,
        encoding="utf-8",
    )

    result = repair_runtime_state(state)
    assert result["failed_missing_agent"] == ["task_a"]

    repaired = (state / "tasks.json").read_text(encoding="utf-8")
    assert '"status": "failed"' in repaired
    assert 'ghost_agent' in repaired
