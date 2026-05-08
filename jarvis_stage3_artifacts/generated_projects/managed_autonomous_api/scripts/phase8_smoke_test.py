from app.services.reliability import ReliabilityManager
from app.services.memory_store import MemoryStore
from app.services.observability import ObservabilityService

reliability = ReliabilityManager()
memory = MemoryStore()
observability = ObservabilityService()

started = reliability.start_reliable_run(
    task_id="phase8_smoke_task_001",
    objective="Implement patch and validate result",
    requested_tools=["python", "filesystem"],
    task_type="code",
    mission_id="phase8_smoke_mission"
)

run = started["run"]
memory.upsert_run_summary(run)
note = memory.add_note("system", "phase8 smoke note", {"run_id": run["run_id"]})
snapshot = observability.snapshot()
timeline = observability.timeline()

print("PHASE8_SMOKE_OK")
print({
    "run_id": run["run_id"],
    "note_id": note["note_id"],
    "snapshot_runs_total": snapshot["runs_total"],
    "timeline_count": timeline["count"]
})
