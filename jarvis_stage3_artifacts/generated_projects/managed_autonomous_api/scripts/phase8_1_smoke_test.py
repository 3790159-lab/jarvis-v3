from app.services.maintenance import MaintenanceService
from app.services.observability import ObservabilityService
from app.services.memory_store import MemoryStore

maintenance = MaintenanceService()
observability = ObservabilityService()
memory = MemoryStore()

norm = maintenance.normalize_legacy_runs()
sync = maintenance.sync_all()
audit = maintenance.audit()
summary = observability.system_summary()
validation = memory.validate_store()

print("PHASE8_1_SMOKE_OK")
print({
    "normalized": norm,
    "sync": sync,
    "audit_ok": audit["ok"],
    "summary_status": summary["status"],
    "memory_ok": validation["ok"]
})
