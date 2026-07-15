"""RCE lockdown — POST /api/tools/execute must require auth.

Audit follow-up: the tool runtime runs arbitrary PowerShell/Python
(``tool_executor_runtime.execute_shell`` → ``subprocess.run(["powershell",...])``)
gated only by a substring denylist. Anonymous reach = remote code execution.
Fix: require an API key; unauthenticated callers never reach ``execute_tool``.
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

KEY = "test-internal-key-rce-tools"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("JARVIS_INTERNAL_API_KEY", KEY)
    monkeypatch.delenv("JARVIS_ADMIN_KEY", raising=False)

    import app.routers.tools_runtime as mod
    importlib.reload(mod)

    calls = []
    monkeypatch.setattr(mod, "execute_tool", lambda tool, payload: calls.append((tool, payload)) or {"ok": True})

    app = FastAPI()
    app.include_router(mod.router)
    return TestClient(app), calls


def test_tools_execute_without_key_is_401_and_no_exec(client):
    c, calls = client
    r = c.post("/api/tools/execute", json={"tool": "shell", "payload": {"command": "whoami"}})
    assert r.status_code == 401
    assert calls == []  # execute_tool never reached


def test_tools_execute_with_key_reaches_runtime(client):
    c, calls = client
    r = c.post(
        "/api/tools/execute",
        headers={"X-API-Key": KEY},
        json={"tool": "shell", "payload": {"command": "whoami"}},
    )
    assert r.status_code == 200
    assert len(calls) == 1
