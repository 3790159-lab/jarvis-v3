# -*- coding: utf-8 -*-
"""DEV-17: app.routers.jarvis_ops_health_router — the HTTP surface Uptime
Kuma polls independently of the bot process. Each endpoint must answer 200
when healthy and 503 when not, since that is the only signal a stock Kuma
HTTP monitor understands (2xx = up, anything else = down). All checks are
monkeypatched here — this suite never touches a real service/file/subprocess.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import jarvis_ops_health_router as router_mod


def _client():
    app = FastAPI()
    app.include_router(router_mod.router)
    return TestClient(app)


def test_heartbeat_endpoint_200_when_ok(monkeypatch):
    monkeypatch.setattr(router_mod.om, "check_bot_heartbeat",
                         lambda *a, **k: {"ok": True, "age_sec": 5.0, "detail": "fresh"})
    resp = _client().get("/api/jarvis/ops/heartbeat")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_heartbeat_endpoint_503_when_stale(monkeypatch):
    monkeypatch.setattr(router_mod.om, "check_bot_heartbeat",
                         lambda *a, **k: {"ok": False, "age_sec": 999.0, "detail": "stale"})
    resp = _client().get("/api/jarvis/ops/heartbeat")
    assert resp.status_code == 503
    assert resp.json()["ok"] is False


def test_cloudflared_endpoint_200_when_running(monkeypatch):
    monkeypatch.setattr(router_mod.om, "check_cloudflared",
                         lambda *a, **k: {"ok": True, "status": "Running", "detail": "Running"})
    resp = _client().get("/api/jarvis/ops/cloudflared")
    assert resp.status_code == 200


def test_cloudflared_endpoint_503_when_not_running(monkeypatch):
    monkeypatch.setattr(router_mod.om, "check_cloudflared",
                         lambda *a, **k: {"ok": False, "status": "Stopped", "detail": "Stopped"})
    resp = _client().get("/api/jarvis/ops/cloudflared")
    assert resp.status_code == 503


def test_disk_endpoint_200_when_ok(monkeypatch):
    monkeypatch.setattr(router_mod.om, "check_disk",
                         lambda *a, **k: {"ok": True, "free_gb": 50.0, "detail": "50GB free"})
    resp = _client().get("/api/jarvis/ops/disk")
    assert resp.status_code == 200


def test_disk_endpoint_503_when_low(monkeypatch):
    monkeypatch.setattr(router_mod.om, "check_disk",
                         lambda *a, **k: {"ok": False, "free_gb": 0.5, "detail": "0.5GB free"})
    resp = _client().get("/api/jarvis/ops/disk")
    assert resp.status_code == 503


def test_restarts_endpoint_200_when_under_threshold(monkeypatch):
    monkeypatch.setattr(router_mod.om, "check_restart_storm",
                         lambda *a, **k: {"ok": True, "count": 1, "threshold": 3, "detail": "1 restart"})
    resp = _client().get("/api/jarvis/ops/restarts")
    assert resp.status_code == 200


def test_restarts_endpoint_503_when_storm(monkeypatch):
    monkeypatch.setattr(router_mod.om, "check_restart_storm",
                         lambda *a, **k: {"ok": False, "count": 5, "threshold": 3, "detail": "5 restarts"})
    resp = _client().get("/api/jarvis/ops/restarts")
    assert resp.status_code == 503
    assert resp.json()["count"] == 5
