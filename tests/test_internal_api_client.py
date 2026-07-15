"""Tests for the shared internal-backend auth header helper.

The default-deny auth middleware (app/services/auth_middleware.py) rejects any
non-public request to :8010 without a valid X-API-Key. In-process/self-call HTTP
consumers (smart_router, scheduler, obsidian savers, ...) must therefore attach
the internal key. This helper is the single place that does it — mirroring the
bot's http_json key-injection, but reusable across urllib/requests callers.
"""
import importlib
import os

import pytest

import app.services.internal_api_client as iac


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # Start each test from a known, key-less env so tests are order-independent.
    for var in (
        "JARVIS_INTERNAL_API_KEY",
        "JARVIS_ADMIN_KEY",
        "BACKEND_BASE_URL",
        "TELEGRAM_BACKEND_URL",
        "JARVIS_BACKEND",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


def test_attaches_internal_key_for_loopback_backend_url(monkeypatch):
    monkeypatch.setenv("JARVIS_INTERNAL_API_KEY", "jvi_secret")
    headers = iac.backend_headers("http://127.0.0.1:8010/api/jarvis/tools/internet/research")
    assert headers["X-API-Key"] == "jvi_secret"


def test_falls_back_to_admin_key_when_internal_absent(monkeypatch):
    monkeypatch.setenv("JARVIS_ADMIN_KEY", "jadmin_secret")
    headers = iac.backend_headers("http://localhost:8010/api/jarvis/tools/table/create")
    assert headers["X-API-Key"] == "jadmin_secret"


def test_internal_key_wins_over_admin_key(monkeypatch):
    monkeypatch.setenv("JARVIS_INTERNAL_API_KEY", "jvi_secret")
    monkeypatch.setenv("JARVIS_ADMIN_KEY", "jadmin_secret")
    headers = iac.backend_headers("http://127.0.0.1:8010/x")
    assert headers["X-API-Key"] == "jvi_secret"


def test_does_not_attach_key_for_external_url(monkeypatch):
    # Safety: never leak the internal key to a non-backend host.
    monkeypatch.setenv("JARVIS_INTERNAL_API_KEY", "jvi_secret")
    headers = iac.backend_headers("https://api.telegram.org/botXXX/sendMessage")
    assert "X-API-Key" not in headers


def test_recognises_configured_backend_host(monkeypatch):
    # A consumer may resolve its base from BACKEND_BASE_URL to a non-loopback host.
    monkeypatch.setenv("JARVIS_INTERNAL_API_KEY", "jvi_secret")
    monkeypatch.setenv("BACKEND_BASE_URL", "http://jarvis-backend.internal:8010")
    headers = iac.backend_headers("http://jarvis-backend.internal:8010/api/jarvis/tools/file/parse")
    assert headers["X-API-Key"] == "jvi_secret"


def test_no_key_configured_returns_headers_without_x_api_key(monkeypatch):
    headers = iac.backend_headers("http://127.0.0.1:8010/x")
    assert "X-API-Key" not in headers


def test_preserves_and_does_not_clobber_existing_headers(monkeypatch):
    monkeypatch.setenv("JARVIS_INTERNAL_API_KEY", "jvi_secret")
    headers = iac.backend_headers(
        "http://127.0.0.1:8010/x",
        {"Content-Type": "application/json; charset=utf-8"},
    )
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert headers["X-API-Key"] == "jvi_secret"


def test_does_not_overwrite_caller_supplied_x_api_key(monkeypatch):
    monkeypatch.setenv("JARVIS_INTERNAL_API_KEY", "jvi_secret")
    headers = iac.backend_headers("http://127.0.0.1:8010/x", {"X-API-Key": "caller_key"})
    assert headers["X-API-Key"] == "caller_key"


def test_returns_new_dict_not_mutating_input(monkeypatch):
    monkeypatch.setenv("JARVIS_INTERNAL_API_KEY", "jvi_secret")
    original = {"Content-Type": "application/json"}
    result = iac.backend_headers("http://127.0.0.1:8010/x", original)
    assert "X-API-Key" not in original  # input untouched
    assert result is not original


def test_reads_key_at_call_time_not_import_time(monkeypatch):
    # The key lands in os.environ via env_bootstrap AFTER this module imports,
    # so it must be read per-call, never cached at import.
    importlib.reload(iac)
    monkeypatch.setenv("JARVIS_INTERNAL_API_KEY", "late_key")
    headers = iac.backend_headers("http://127.0.0.1:8010/x")
    assert headers["X-API-Key"] == "late_key"
