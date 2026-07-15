"""H3 security fix — operator-brain command channel must require auth.

Audit 2026-07-15 finding H4 (user task H3): ``/api/jarvis/live-command`` and
``/api/jarvis/respond-live`` fed arbitrary text into
``JarvisLiveOperatorBrain().handle()`` with no authorization. Fix: gate both
POST endpoints behind the same API-key dependency as the rest of the surface,
and never touch the brain for an unauthenticated request.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KEY = "test-internal-key-h3"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("JARVIS_INTERNAL_API_KEY", KEY)
    monkeypatch.delenv("JARVIS_ADMIN_KEY", raising=False)

    import importlib
    import app.routers.jarvis_live_operator as mod
    importlib.reload(mod)

    # Spy: prove the brain is never invoked for an unauthenticated call.
    calls = []
    original_handle = mod.JarvisLiveOperatorBrain.handle

    def _spy_handle(self, *a, **k):
        calls.append((a, k))
        return {"ok": True, "message": "handled"}

    monkeypatch.setattr(mod.JarvisLiveOperatorBrain, "handle", _spy_handle)

    app = FastAPI()
    app.include_router(mod.router)
    return TestClient(app), calls


def test_live_command_without_key_is_401_and_brain_untouched(client):
    c, calls = client
    r = c.post("/api/jarvis/live-command", json={"text": "do something"})
    assert r.status_code == 401
    assert calls == []


def test_respond_live_without_key_is_401_and_brain_untouched(client):
    c, calls = client
    r = c.post("/api/jarvis/respond-live", json={"text": "do something"})
    assert r.status_code == 401
    assert calls == []


def test_live_command_with_key_reaches_brain(client):
    c, calls = client
    r = c.post(
        "/api/jarvis/live-command",
        headers={"X-API-Key": KEY},
        json={"text": "status?"},
    )
    assert r.status_code == 200
    assert len(calls) == 1
