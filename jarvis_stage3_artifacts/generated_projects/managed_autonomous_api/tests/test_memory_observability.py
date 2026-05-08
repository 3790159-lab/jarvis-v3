from app.services.reliability import ReliabilityManager
from app.services.memory_store import MemoryStore
from app.services.observability import ObservabilityService
from app.services.maintenance import MaintenanceService


def test_memory_sync_and_lookup():
    reliability = ReliabilityManager()
    store = MemoryStore()

    started = reliability.start_reliable_run(
        task_id="phase8_1_task_001",
        objective="Implement patch and validate result",
        requested_tools=["python", "filesystem"],
        task_type="code",
        mission_id="phase8_1_mission"
    )

    run = started["run"]
    summary = store.upsert_run_summary(run)

    assert summary["run_id"] == run["run_id"]
    assert store.get_run_summary(run["run_id"]) is not None
    assert store.get_mission_summary("phase8_1_mission") is not None


def test_notes_validation():
    store = MemoryStore()
    note = store.add_note("system", "phase8.1 note", {"phase": "8.1"})
    assert note["type"] == "system"

    try:
        store.add_note("", "bad", {})
        assert False, "Expected ValueError for empty note_type"
    except ValueError:
        pass


def test_observability_summary():
    service = ObservabilityService()
    summary = service.system_summary()
    assert summary["status"] == "ok"
    assert "snapshot" in summary
    assert "memory_validation" in summary


def test_maintenance_audit():
    service = MaintenanceService()
    audit = service.audit()
    assert "ok" in audit
    assert "warnings" in audit
