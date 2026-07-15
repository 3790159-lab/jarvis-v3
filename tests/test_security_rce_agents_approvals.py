"""RCE lockdown — agent invoke + approval-approve must require auth.

``/api/agents/invoke`` spawns adapters (Claude CLI subprocess / paid APIs);
``/api/approvals/{id}/approve`` lets a caller approve a pending request. Together,
anonymously, they are a self-approve → invoke bypass. Fix: require an API key on
both; unauthenticated callers never reach the risk policy or the approval store.
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

KEY = "test-internal-key-rce-agents"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("JARVIS_INTERNAL_API_KEY", KEY)
    monkeypatch.delenv("JARVIS_ADMIN_KEY", raising=False)

    import app.routers.agent_control_plane as mod
    importlib.reload(mod)

    risk_calls = []
    approve_calls = []
    monkeypatch.setattr(mod, "evaluate_risk", lambda **k: risk_calls.append(k) or {"is_forbidden": True, "reason": "x", "requires_approval": False, "risk_level": "low"})
    monkeypatch.setattr(mod, "set_approval_status", lambda *a, **k: approve_calls.append((a, k)) or {"id": "x"})

    app = FastAPI()
    app.include_router(mod.router)
    return TestClient(app), risk_calls, approve_calls


def test_agents_invoke_without_key_is_401_and_no_policy(client):
    c, risk_calls, _ = client
    r = c.post("/api/agents/invoke", json={"adapter_name": "claude_code_bridge", "payload": {}})
    assert r.status_code == 401
    assert risk_calls == []  # evaluate_risk never reached


def test_approval_approve_without_key_is_401_and_no_mutation(client):
    c, _, approve_calls = client
    r = c.post("/api/approvals/abc123/approve", json={})
    assert r.status_code == 401
    assert approve_calls == []  # set_approval_status never reached


def test_agents_invoke_with_key_reaches_policy(client):
    c, risk_calls, _ = client
    r = c.post(
        "/api/agents/invoke",
        headers={"X-API-Key": KEY},
        json={"adapter_name": "claude_code_bridge", "payload": {}},
    )
    # Policy runs and forbids (403) — the point is auth passed and the handler ran.
    assert r.status_code == 403
    assert len(risk_calls) == 1
