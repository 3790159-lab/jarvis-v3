"""Phase 1 (canary) of the default-deny auth middleware.

The middleware computes an allow/deny decision for every request using the same
logic the future enforce phase will use, but in canary mode it only LOGS/records
would-deny requests and always passes them through. This surfaces every internal
caller that lacks a key before anything is blocked.
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

KEY = "test-internal-key-canary"


@pytest.fixture()
def mod(monkeypatch):
    monkeypatch.setenv("JARVIS_INTERNAL_API_KEY", KEY)
    monkeypatch.delenv("JARVIS_ADMIN_KEY", raising=False)
    import importlib
    import app.services.auth_middleware as m
    importlib.reload(m)
    m._seen.clear()
    return m


# ── pure decision logic ─────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "path, expected",
    [
        ("/health", True),
        ("/api/tools/health", True),          # */health suffix
        ("/api/autonomy/health", True),
        ("/oauth/callback", True),
        ("/telegram/webhook", True),          # self-secured by its own token
        ("/api/tools/execute", False),
        ("/api/autonomy/tools/execute", False),
        ("/docs", False),                     # gated: leaks API surface
        ("/openapi.json", False),
        ("/", False),
    ],
)
def test_is_public_path(mod, path, expected):
    assert mod.is_public_path(path) is expected


def test_classify_public_allows_without_key(mod):
    assert mod.classify("/health", None) == "allow"


def test_classify_nonpublic_denies_without_key(mod):
    assert mod.classify("/api/tools/execute", None) == "deny"


def test_classify_nonpublic_denies_with_bad_key(mod):
    assert mod.classify("/api/tools/execute", "wrong") == "deny"


def test_classify_nonpublic_allows_with_valid_key(mod):
    assert mod.classify("/api/tools/execute", KEY) == "allow"


# ── canary recording ────────────────────────────────────────────────────────

def test_record_canary_event_writes_and_dedups(mod, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ev = {"method": "POST", "path": "/api/tools/execute", "client": "127.0.0.1", "user_agent": "x", "had_key": False}
    assert mod.record_canary_event(ev) is True
    assert mod.record_canary_event(ev) is False  # deduped
    lines = (tmp_path / "state" / "auth_canary.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


# ── middleware wiring ───────────────────────────────────────────────────────

def _app(mod):
    app = FastAPI()
    app.middleware("http")(mod.auth_guard_middleware)

    @app.get("/secret")
    def secret():
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"ok": True}

    return app


def test_canary_logs_deny_but_passes_through(mod, monkeypatch):
    monkeypatch.setenv("JARVIS_AUTH_MIDDLEWARE_MODE", "canary")
    recorded = []
    monkeypatch.setattr(mod, "record_canary_event", lambda ev: recorded.append(ev) or True)
    c = TestClient(_app(mod))
    r = c.get("/secret")
    assert r.status_code == 200          # never blocked in canary
    assert len(recorded) == 1
    assert recorded[0]["path"] == "/secret"
    assert recorded[0]["had_key"] is False


def test_canary_ignores_public_and_keyed(mod, monkeypatch):
    monkeypatch.setenv("JARVIS_AUTH_MIDDLEWARE_MODE", "canary")
    recorded = []
    monkeypatch.setattr(mod, "record_canary_event", lambda ev: recorded.append(ev) or True)
    c = TestClient(_app(mod))
    assert c.get("/health").status_code == 200
    assert c.get("/secret", headers={"X-API-Key": KEY}).status_code == 200
    assert recorded == []


def test_off_mode_records_nothing(mod, monkeypatch):
    monkeypatch.setenv("JARVIS_AUTH_MIDDLEWARE_MODE", "off")
    recorded = []
    monkeypatch.setattr(mod, "record_canary_event", lambda ev: recorded.append(ev) or True)
    c = TestClient(_app(mod))
    assert c.get("/secret").status_code == 200
    assert recorded == []
