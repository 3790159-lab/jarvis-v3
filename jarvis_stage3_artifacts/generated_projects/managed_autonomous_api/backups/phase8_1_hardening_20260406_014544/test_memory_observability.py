from app.services.reliability import ReliabilityManager
from app.services.memory_store import MemoryStore
from app.services.observability import ObservabilityService


def test_memory_sync_and_lookup():
    reliability = ReliabilityManager()
    store = MemoryStore()

    started = reliability.start_reliable_run(
        task_id="phase8_task_001",
        objective="Implement patch and validate result",
        requested_tools=["python", "filesystem"],
        task_type="code",
        mission_id="phase8_mission"
    )

    run = started["run"]
    summary = store.upsert_run_summary(run)

    assert summary["run_id"] == run["run_id"]
    assert store.get_run_summary(run["run_id"]) is not None
    assert store.get_mission_summary("phase8_mission") is not None


def test_notes_can_be_added():
    store = MemoryStore()
    note = store.add_note("system", "phase8 note", {"phase": 8})
    assert note["type"] == "system"
    assert note["text"] == "phase8 note"


def test_observability_snapshot():
    reliability = ReliabilityManager()
    reliability.start_reliable_run(
        task_id="phase8_task_002",
        objective="Run execution flow",
        requested_tools=["shell"],
        task_type="execute",
        mission_id="phase8_mission"
    )

    service = ObservabilityService()
    snapshot = service.snapshot()

    assert "runs_total" in snapshot
    assert "run_statuses" in snapshot
    assert snapshot["runs_total"] >= 1


def test_observability_timeline():
    service = ObservabilityService()
    timeline = service.timeline()
    assert "items" in timeline
    assert isinstance(timeline["items"], list)
