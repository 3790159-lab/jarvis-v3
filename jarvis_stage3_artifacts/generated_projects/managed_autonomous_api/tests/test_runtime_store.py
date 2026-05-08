from app.runtime import TaskRecord, tasks_store


def test_task_store_uses_task_id_key():
    task = TaskRecord(
        task_id="task_test_001",
        mission_id=None,
        task_type="echo",
        status="queued",
        created_at=1.0,
        updated_at=1.0,
        payload={"message": "hello"},
        assigned_agent="executor_agent",
    )

    tasks_store.upsert(task)
    fetched = tasks_store.get("task_test_001", refresh=True)

    assert fetched is not None
    assert fetched.task_id == "task_test_001"
