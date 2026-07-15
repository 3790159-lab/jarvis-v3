"""RCE lockdown — /api/cloud/* proxy must require auth and not bypass target gates.

``cloud_control`` proxies to internal execute endpoints (tool/agent/google/...).
Anonymous access = RCE-by-proxy + SSRF. Fix: require an API key on the proxy
endpoints, and have the internal ``_call`` forward the internal key so a proxied
request is only ever as privileged as an authenticated caller (no gate bypass).
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

KEY = "test-internal-key-rce-cloud"


@pytest.fixture()
def mod(monkeypatch):
    monkeypatch.setenv("JARVIS_INTERNAL_API_KEY", KEY)
    monkeypatch.delenv("JARVIS_ADMIN_KEY", raising=False)
    import app.api.cloud_control as m
    importlib.reload(m)
    return m


@pytest.fixture()
def client(mod):
    app = FastAPI()
    app.include_router(mod.router)
    return TestClient(app)


@pytest.mark.parametrize(
    "path, body",
    [
        ("/api/cloud/execute", {"target": "tool", "payload": {"command": "whoami"}}),
        ("/api/cloud/raw-invoke", {"method": "POST", "path": "/api/tools/execute", "payload": {}}),
        ("/api/cloud/plan-and-execute", {"goal": "x"}),
    ],
)
def test_cloud_endpoints_without_key_are_401(client, path, body):
    r = client.post(path, json=body)
    assert r.status_code == 401


def test_call_forwards_internal_api_key(mod, monkeypatch):
    """The proxy authenticates to the (now-gated) target instead of bypassing it."""
    captured = {}

    class _Resp:
        status_code = 200
        headers = {"content-type": "application/json"}

        def json(self):
            return {"ok": True}

    def _fake_post(url, json=None, timeout=None, headers=None):
        captured["headers"] = headers or {}
        return _Resp()

    monkeypatch.setattr(mod.requests, "post", _fake_post)
    mod._call("POST", "/api/tools/execute", payload={"x": 1})
    assert captured["headers"].get("X-API-Key") == KEY
