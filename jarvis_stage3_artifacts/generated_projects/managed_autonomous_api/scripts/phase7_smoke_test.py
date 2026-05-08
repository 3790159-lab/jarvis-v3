from app.services.reliability import ReliabilityManager

manager = ReliabilityManager()

first = manager.start_reliable_run(
    task_id="phase7_smoke_001",
    objective="Implement endpoint patch and validate result",
    requested_tools=["python", "filesystem"],
    task_type="code",
    mission_id="phase7_smoke_mission"
)

run_id = first["run"]["run_id"]

manager.mark_stage(run_id, "stage_1", "completed", "planner passed")
manager.create_retry(run_id, "simulated transient issue")
manager.quarantine_run(run_id, "simulated quarantine check")

print("PHASE7_SMOKE_OK")
print({
    "first_status": first["status"],
    "run_id": run_id,
    "final_run": manager.get_run(run_id)
})
