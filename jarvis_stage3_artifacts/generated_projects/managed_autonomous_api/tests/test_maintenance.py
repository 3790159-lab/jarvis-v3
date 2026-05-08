from app.services.maintenance import MaintenanceService


def test_maintenance_endpoints_logic():
    service = MaintenanceService()
    norm = service.normalize_legacy_runs()
    assert "fixed_runs" in norm

    sync = service.sync_all()
    assert sync["status"] == "ok"

    audit = service.audit()
    assert "ok" in audit
