from app.services.reliability import ReliabilityManager, build_idempotency_key


def test_idempotency_key_is_stable():
    a = build_idempotency_key("task1", "Run patch", ["python", "filesystem"], "code")
    b = build_idempotency_key("task1", "Run patch", ["filesystem", "python"], "code")
    assert a == b
    assert len(a) == 24


def test_start_reliable_run_starts_first_stage_running():
    manager = ReliabilityManager()
    started = manager.start_reliable_run(
        task_id="phase7_1_task_001",
        objective="Implement patch and validate result",
        requested_tools=["python", "filesystem"],
        task_type="code",
        mission_id="phase7_1_mission"
    )
    assert started["status"] == "ok"
    assert started["run"]["status"] == "running"
    assert started["run"]["stages"][0]["status"] == "running"


def test_mark_stage_updates_run_status():
    manager = ReliabilityManager()
    started = manager.start_reliable_run(
        task_id="phase7_1_task_002",
        objective="Implement patch and validate result 2",
        requested_tools=["python", "filesystem"],
        task_type="code",
        mission_id="phase7_1_mission"
    )
    run_id = started["run"]["run_id"]

    updated = manager.mark_stage(run_id, "stage_1", "completed", "planner ok")
    assert updated["status"] == "running"


def test_invalid_stage_name_raises():
    manager = ReliabilityManager()
    started = manager.start_reliable_run(
        task_id="phase7_1_task_003",
        objective="Run execution flow",
        requested_tools=["shell"],
        task_type="execute",
        mission_id="phase7_1_mission"
    )
    run_id = started["run"]["run_id"]

    try:
        manager.mark_stage(run_id, "bad_stage", "completed")
        assert False, "Expected ValueError for bad_stage"
    except ValueError as exc:
        assert "Stage not found" in str(exc)


def test_quarantine_blocks_pending_stages():
    manager = ReliabilityManager()
    started = manager.start_reliable_run(
        task_id="phase7_1_task_004",
        objective="Run execution flow 2",
        requested_tools=["shell"],
        task_type="execute",
        mission_id="phase7_1_mission"
    )
    run_id = started["run"]["run_id"]

    quarantined = manager.quarantine_run(run_id, "manual stop")
    assert quarantined["status"] == "quarantined"
    blocked = [s for s in quarantined["stages"] if s["status"] == "blocked"]
    assert len(blocked) >= 1


def test_retry_reopens_stage():
    manager = ReliabilityManager()
    started = manager.start_reliable_run(
        task_id="phase7_1_task_005",
        objective="Run execution flow 3",
        requested_tools=["shell"],
        task_type="execute",
        mission_id="phase7_1_mission"
    )
    run_id = started["run"]["run_id"]

    manager.mark_stage(run_id, "stage_1", "failed", "transient fail")
    retried = manager.create_retry(run_id, "retry test")
    assert retried["status"] == "retrying"
    assert any(s["status"] == "running" for s in retried["stages"])
