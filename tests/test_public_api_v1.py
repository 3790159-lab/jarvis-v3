"""Phase 44: Public API v1 tests — auth + rate limiting + key management."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.api.public_api_v1 as api_mod
from app.api.public_api_v1 import (
    validate_api_key,
    _check_rate_limit,
    _load_keys,
    _save_keys,
    _is_admin,
    _extract_api_key,
    _RATE_LIMIT,
    _RATE_WINDOW,
)


# ---------------------------------------------------------------------------
# validate_api_key
# ---------------------------------------------------------------------------

class TestValidateApiKey:
    def test_empty_key_invalid(self):
        assert validate_api_key("") is False

    def test_valid_active_key(self, monkeypatch, tmp_path):
        monkeypatch.setattr(api_mod, "_API_KEYS_PATH", tmp_path / "api_keys.json")
        _save_keys({"test_key_abc": {"active": True, "label": "test"}})
        assert validate_api_key("test_key_abc") is True

    def test_inactive_key_rejected(self, monkeypatch, tmp_path):
        monkeypatch.setattr(api_mod, "_API_KEYS_PATH", tmp_path / "api_keys.json")
        _save_keys({"test_key_xyz": {"active": False, "label": "test"}})
        assert validate_api_key("test_key_xyz") is False

    def test_unknown_key_rejected(self, monkeypatch, tmp_path):
        monkeypatch.setattr(api_mod, "_API_KEYS_PATH", tmp_path / "api_keys.json")
        _save_keys({})
        assert validate_api_key("unknown_key") is False


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

class TestRateLimiting:
    def setup_method(self):
        api_mod._rate_store.clear()

    def test_allows_under_limit(self):
        key = "rate_test_key"
        for _ in range(_RATE_LIMIT - 1):
            assert _check_rate_limit(key) is True

    def test_blocks_at_limit(self):
        key = "rate_block_key"
        for _ in range(_RATE_LIMIT):
            _check_rate_limit(key)
        assert _check_rate_limit(key) is False

    def test_different_keys_independent(self):
        key_a = "rate_key_a"
        key_b = "rate_key_b"
        for _ in range(_RATE_LIMIT):
            _check_rate_limit(key_a)
        # key_b should still be allowed
        assert _check_rate_limit(key_b) is True

    def test_window_resets_old_calls(self, monkeypatch):
        key = "rate_window_key"
        api_mod._rate_store[key] = [time.time() - _RATE_WINDOW - 10] * _RATE_LIMIT
        assert _check_rate_limit(key) is True


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------

class TestKeyManagement:
    def test_save_and_load_keys(self, monkeypatch, tmp_path):
        monkeypatch.setattr(api_mod, "_API_KEYS_PATH", tmp_path / "api_keys.json")
        keys = {"key1": {"active": True, "label": "test"}}
        _save_keys(keys)
        loaded = _load_keys()
        assert loaded["key1"]["active"] is True

    def test_load_returns_empty_when_no_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(api_mod, "_API_KEYS_PATH", tmp_path / "nonexistent.json")
        assert _load_keys() == {}


# ---------------------------------------------------------------------------
# Admin auth
# ---------------------------------------------------------------------------

class TestAdminAuth:
    def test_admin_key_check(self, monkeypatch):
        monkeypatch.setenv("JARVIS_ADMIN_KEY", "super_secret_admin")
        assert _is_admin("super_secret_admin") is True
        assert _is_admin("wrong_key") is False

    def test_no_admin_key_set(self, monkeypatch):
        monkeypatch.delenv("JARVIS_ADMIN_KEY", raising=False)
        assert _is_admin("any_key") is False


# ---------------------------------------------------------------------------
# _extract_api_key
# ---------------------------------------------------------------------------

class TestExtractApiKey:
    def _make_request(self, headers: dict = None, params: dict = None):
        req = MagicMock()
        req.headers = headers or {}
        req.query_params = params or {}
        return req

    def test_from_header(self):
        req = self._make_request(headers={"X-API-Key": "header_key"})
        assert _extract_api_key(req) == "header_key"

    def test_from_query_param(self):
        req = self._make_request(params={"api_key": "query_key"})
        assert _extract_api_key(req) == "query_key"

    def test_header_takes_precedence(self):
        req = self._make_request(
            headers={"X-API-Key": "header_key"},
            params={"api_key": "query_key"}
        )
        assert _extract_api_key(req) == "header_key"

    def test_empty_when_missing(self):
        req = self._make_request()
        assert _extract_api_key(req) == ""


# ---------------------------------------------------------------------------
# Router endpoints
# ---------------------------------------------------------------------------

class TestRouterEndpoints:
    def test_ask_route_registered(self):
        from app.api.public_api_v1 import router
        paths = [r.path for r in router.routes]
        assert "/api/v1/ask" in paths

    def test_agents_route_registered(self):
        from app.api.public_api_v1 import router
        paths = [r.path for r in router.routes]
        assert "/api/v1/agents" in paths

    def test_status_route_registered(self):
        from app.api.public_api_v1 import router
        paths = [r.path for r in router.routes]
        assert "/api/v1/status" in paths

    def test_admin_create_key_route_registered(self):
        from app.api.public_api_v1 import router
        paths = [r.path for r in router.routes]
        assert "/api/v1/admin/api-keys" in paths
