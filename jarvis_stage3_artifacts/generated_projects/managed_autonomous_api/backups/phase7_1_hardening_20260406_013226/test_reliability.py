from app.services.reliability import ReliabilityManager, build_idempotency_key


def test_idempotency_key_is_stable():
    a = build_idempotency_key("task1", "Run patch", ["python", "filesystem"], "code")
    b = build_idempotency_key("task1", "Run patch", ["filesystem", "python"], "code")
    assert a == b
    assert len(a) == 24


def test_start_reliable_run_and_duplicate_detection():
    manager = ReliabilityManager()

    first = manager.start_reliable_run(
        task_id="phase7_task_001",
        objective="Implement patch and validate result",
        requested_tools=["python", "filesystem"],
        task_type="code",
        mission_id="phase7_mission"
    )
    assert first["status"] == "ok"
    run_id = first["run"]["run_id"]

    second = manager.start_reliable_run(
        task_id="phase7_task_001",
        objective="Implement patch and validate result",
        requested_tools=["filesystem", "python"],
        task_type="code",
        mission_id="phase7_mission"
    )
    assert second["status"] == "duplicate"
    assert second["run"]["run_id"] == run_id


def test_stage_update_retry_and_quarantine():
    manager = ReliabilityManager()
    started = manager.start_reliable_run(
        task_id="phase7_task_002",
        objective="Run execution flow",
        requested_tools=["shell"],
        task_type="execute",
        mission_id="phase7_mission"
    )
    run_id = started["run"]["run_id"]

    updated = manager.mark_stage(run_id, "stage_1", "completed", "planner ok")
    assert any(x["stage_name"] == "stage_1" and x["status"] == "completed" for x in updated["stages"])

    retried = manager.create_retry(run_id, "transient failure")
    assert retried["status"] == "retrying"
    assert retried["retry_count"] >= 1

    quarantined = manager.quarantine_run(run_id, "manual safety stop")
    assert quarantined["status"] == "quarantined"
    assert quarantined["quarantine_reason"] == "manual safety stop"
