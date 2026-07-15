"""RCE lockdown — brain-executor/run and external-executor/execute need auth.

``/api/brain-executor/run`` compiles an arbitrary task and routes it to the n8n
super-agent / execution pipeline; ``/api/external-executor/execute`` dispatches
prompts to paid cloud AI providers. Both were anonymous. Fix: require an API key;
unauthenticated callers never reach the executor.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KEY = "test-internal-key-rce-brain"


@pytest.fixture()
def brain_client(monkeypatch):
    monkeypatch.setenv("JARVIS_INTERNAL_API_KEY", KEY)
    monkeypatch.delenv("JARVIS_ADMIN_KEY", raising=False)
    import app.routers.jarvis_brain_executor_router as mod
    importlib.reload(mod)
    calls = []
    monkeypatch.setattr(mod, "_executor", lambda: calls.append(1) or (_ for _ in ()).throw(AssertionError("reached")))
    app = FastAPI()
    app.include_router(mod.router)
    return TestClient(app, raise_server_exceptions=False), calls


@pytest.fixture()
def external_client(monkeypatch):
    monkeypatch.setenv("JARVIS_INTERNAL_API_KEY", KEY)
    monkeypatch.delenv("JARVIS_ADMIN_KEY", raising=False)
    import app.api.external_executor as mod
    importlib.reload(mod)
    app = FastAPI()
    app.include_router(mod.router)
    return TestClient(app, raise_server_exceptions=False)


def test_brain_run_without_key_is_401_and_no_exec(brain_client):
    c, calls = brain_client
    r = c.post("/api/brain-executor/run", json={"task": "rm -rf /"})
    assert r.status_code == 401
    assert calls == []  # _executor never constructed


def test_external_execute_without_key_is_401(external_client):
    r = external_client.post("/api/external-executor/execute", json={"title": "x"})
    assert r.status_code == 401
