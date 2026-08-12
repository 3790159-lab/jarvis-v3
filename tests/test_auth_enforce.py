"""Phase 2 — enforce mode + finalized public allow-list.

Canary (Phase 1) surfaced no organic denials (api.* is cut from ingress), so the
allow-list is finalized from the static trace: /health + */health, /oauth/callback,
/telegram/webhook (self-secured), and the /api/jarvis/ops/ prefix (Uptime Kuma
liveness). Enforce returns 401 on deny; loopback is never trusted.
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

KEY = "test-internal-key-enforce"


@pytest.fixture()
def mod(monkeypatch):
    monkeypatch.setenv("JARVIS_INTERNAL_API_KEY", KEY)
    monkeypatch.delenv("JARVIS_ADMIN_KEY", raising=False)
    import app.services.auth_middleware as m
    importlib.reload(m)
    m._seen.clear()
    return m


# ── finalized allow-list ────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "path, expected",
    [
        ("/health", True),
        ("/api/tools/health", True),
        ("/oauth/callback", True),
        ("/telegram/webhook", True),
        ("/api/jarvis/ops/heartbeat", True),   # Uptime Kuma liveness
        ("/api/jarvis/ops/cloudflared", True),
        ("/api/jarvis/ops/disk", True),
        ("/api/jarvis/ops/restarts", True),
        # Панель ходит не по X-API-Key, а по ключу владельца в query/cookie,
        # которого этот middleware не видит. Без публичного префикса вход с
        # телефона получает 401 ещё до роутера. Защита субтри — require_owner
        # на роутерах, её держит tests/test_panel_routes_owner_guarded.py.
        ("/panel/login", True),
        ("/panel/jarvis", True),
        ("/panel/jarvis/api/snapshot", True),
        ("/panel/tamapi", True),
        ("/api/tools/execute", False),
        ("/api/jarvis/image/generate", False),
        ("/api/jarvis/tools/internet/research", False),
        ("/docs", False),
        ("/api/jarvis/ops", False),            # exact, no trailing slash — not the prefix
        # Голый /panel — короткий адрес, который набирают с телефона: он лишь
        # разводит на панель или на форму входа. Открыт ТОЧНЫМ совпадением, а не
        # префиксом, иначе публичным станет и `/panelling/secret` строкой ниже.
        ("/panel", True),
        ("/panelling/secret", False),          # префикс — это /panel/, а не /panel
    ],
)
def test_is_public_path_final(mod, path, expected):
    assert mod.is_public_path(path) is expected


# ── enforce behaviour ───────────────────────────────────────────────────────

def _app(mod):
    app = FastAPI()
    app.middleware("http")(mod.auth_guard_middleware)

    @app.get("/secret")
    def secret():
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/api/jarvis/ops/heartbeat")
    def ops():
        return {"ok": True}

    return app


def test_enforce_denies_unkeyed_nonpublic(mod, monkeypatch):
    monkeypatch.setenv("JARVIS_AUTH_MIDDLEWARE_MODE", "enforce")
    c = TestClient(_app(mod))
    assert c.get("/secret").status_code == 401


def test_enforce_allows_public(mod, monkeypatch):
    monkeypatch.setenv("JARVIS_AUTH_MIDDLEWARE_MODE", "enforce")
    c = TestClient(_app(mod))
    assert c.get("/health").status_code == 200
    assert c.get("/api/jarvis/ops/heartbeat").status_code == 200


def test_enforce_allows_valid_key(mod, monkeypatch):
    monkeypatch.setenv("JARVIS_AUTH_MIDDLEWARE_MODE", "enforce")
    c = TestClient(_app(mod))
    assert c.get("/secret", headers={"X-API-Key": KEY}).status_code == 200


def test_enforce_rejects_bad_key(mod, monkeypatch):
    monkeypatch.setenv("JARVIS_AUTH_MIDDLEWARE_MODE", "enforce")
    c = TestClient(_app(mod))
    assert c.get("/secret", headers={"X-API-Key": "wrong"}).status_code == 401


def test_canary_still_passes_through(mod, monkeypatch):
    monkeypatch.setenv("JARVIS_AUTH_MIDDLEWARE_MODE", "canary")
    c = TestClient(_app(mod))
    assert c.get("/secret").status_code == 200  # canary never blocks


def test_unknown_mode_is_failsafe_nonblocking(mod, monkeypatch):
    """Any mode other than 'enforce'/'off' must NOT block (fail-safe)."""
    monkeypatch.setenv("JARVIS_AUTH_MIDDLEWARE_MODE", "banana")
    c = TestClient(_app(mod))
    assert c.get("/secret").status_code == 200


def test_off_mode_passes(mod, monkeypatch):
    monkeypatch.setenv("JARVIS_AUTH_MIDDLEWARE_MODE", "off")
    c = TestClient(_app(mod))
    assert c.get("/secret").status_code == 200
