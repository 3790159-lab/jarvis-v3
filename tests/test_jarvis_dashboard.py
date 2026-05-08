"""Phase 40: Tests for Jarvis Web Dashboard."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.routers.jarvis_dashboard_router import (
    _fmt_uptime,
    _get_status,
    _is_authorized,
    _START_TIME,
)


# ---------------------------------------------------------------------------
# _fmt_uptime
# ---------------------------------------------------------------------------

class TestFmtUptime:
    def test_seconds_only(self):
        assert _fmt_uptime(45) == "45s"

    def test_minutes(self):
        assert _fmt_uptime(90) == "1m 30s"

    def test_hours_minutes(self):
        assert _fmt_uptime(3665) == "1h 1m"

    def test_zero(self):
        assert _fmt_uptime(0) == "0s"

    def test_exact_minute(self):
        assert _fmt_uptime(60) == "1m 0s"

    def test_exact_hour(self):
        assert _fmt_uptime(3600) == "1h 0m"

    def test_two_hours(self):
        assert _fmt_uptime(7261) == "2h 1m"


# ---------------------------------------------------------------------------
# _get_status
# ---------------------------------------------------------------------------

class TestGetStatus:
    def test_returns_required_keys(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "state").mkdir()
        status = _get_status()
        for key in ("status", "uptime_seconds", "uptime_human", "agents",
                    "scheduled_tasks", "decisions_today", "errors_today", "timestamp"):
            assert key in status, f"Missing key: {key}"

    def test_status_is_online(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "state").mkdir()
        assert _get_status()["status"] == "online"

    def test_uptime_positive(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "state").mkdir()
        assert _get_status()["uptime_seconds"] >= 0

    def test_agents_keys_present(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "state").mkdir()
        agents = _get_status()["agents"]
        for key in ("replicate", "openai", "anthropic"):
            assert key in agents

    def test_replicate_configured(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "state").mkdir()
        monkeypatch.setenv("REPLICATE_API_KEY", "r8_test")
        agents = _get_status()["agents"]
        assert agents["replicate"] == "ok"

    def test_replicate_not_configured(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "state").mkdir()
        monkeypatch.delenv("REPLICATE_API_KEY", raising=False)
        agents = _get_status()["agents"]
        assert agents["replicate"] == "not_configured"

    def test_decisions_today_counted(self, monkeypatch, tmp_path):
        import app.routers.jarvis_dashboard_router as mod
        monkeypatch.setattr(mod, "_ROOT", tmp_path)
        state = tmp_path / "state"
        state.mkdir()
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        lines = [
            json.dumps({"timestamp": f"{today}T10:00:00Z", "intent": "research"}),
            json.dumps({"timestamp": f"{today}T11:00:00Z", "intent": "simple_question"}),
            json.dumps({"timestamp": "2020-01-01T10:00:00Z", "intent": "research"}),
        ]
        (state / "decisions.jsonl").write_text("\n".join(lines), encoding="utf-8")
        status = _get_status()
        assert status["decisions_today"] == 2

    def test_scheduled_tasks_counted(self, monkeypatch, tmp_path):
        import app.routers.jarvis_dashboard_router as mod
        monkeypatch.setattr(mod, "_ROOT", tmp_path)
        state = tmp_path / "state"
        state.mkdir()
        tasks = [{"id": "1", "active": True}, {"id": "2", "active": False}, {"id": "3", "active": True}]
        (state / "scheduled_tasks.json").write_text(json.dumps(tasks), encoding="utf-8")
        status = _get_status()
        assert status["scheduled_tasks"] == 2


# ---------------------------------------------------------------------------
# _is_authorized
# ---------------------------------------------------------------------------

class TestIsAuthorized:
    def _mock_request(self, params: dict = None, cookies: dict = None):
        req = MagicMock()
        req.query_params = params or {}
        req.cookies = cookies or {}
        return req

    def test_open_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_ID", raising=False)
        assert _is_authorized(self._mock_request()) is True

    def test_authorized_via_query_param(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "123456")
        req = self._mock_request(params={"chat_id": "123456"})
        assert _is_authorized(req) is True

    def test_unauthorized_wrong_id(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "123456")
        req = self._mock_request(params={"chat_id": "999999"})
        assert _is_authorized(req) is False

    def test_authorized_via_cookie(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "123456")
        req = self._mock_request(cookies={"jarvis_chat_id": "123456"})
        assert _is_authorized(req) is True

    def test_unauthorized_no_param(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "123456")
        assert _is_authorized(self._mock_request()) is False


# ---------------------------------------------------------------------------
# Dashboard HTML content
# ---------------------------------------------------------------------------

class TestDashboardHtml:
    def test_html_contains_chart_js(self):
        from app.routers.jarvis_dashboard_router import _DASHBOARD_HTML
        assert "chart.js" in _DASHBOARD_HTML.lower() or "Chart" in _DASHBOARD_HTML

    def test_html_has_chat_input(self):
        from app.routers.jarvis_dashboard_router import _DASHBOARD_HTML
        assert "chat-input" in _DASHBOARD_HTML

    def test_html_has_websocket_connect(self):
        from app.routers.jarvis_dashboard_router import _DASHBOARD_HTML
        assert "WebSocket" in _DASHBOARD_HTML

    def test_html_has_status_cards(self):
        from app.routers.jarvis_dashboard_router import _DASHBOARD_HTML
        assert "c-uptime" in _DASHBOARD_HTML
        assert "c-decisions" in _DASHBOARD_HTML

    def test_html_sends_to_dashboard_api_chat(self):
        from app.routers.jarvis_dashboard_router import _DASHBOARD_HTML
        assert "/dashboard/api/chat" in _DASHBOARD_HTML

    def test_html_has_jarvis_title(self):
        from app.routers.jarvis_dashboard_router import _DASHBOARD_HTML
        assert "Jarvis" in _DASHBOARD_HTML


# ---------------------------------------------------------------------------
# Router endpoints exist
# ---------------------------------------------------------------------------

class TestRouterEndpoints:
    def test_dashboard_route_registered(self):
        from app.routers.jarvis_dashboard_router import router
        paths = [r.path for r in router.routes]
        assert "/dashboard" in paths

    def test_status_api_route_registered(self):
        from app.routers.jarvis_dashboard_router import router
        paths = [r.path for r in router.routes]
        assert "/dashboard/api/status" in paths

    def test_chat_api_route_registered(self):
        from app.routers.jarvis_dashboard_router import router
        paths = [r.path for r in router.routes]
        assert "/dashboard/api/chat" in paths

    def test_ws_route_registered(self):
        from app.routers.jarvis_dashboard_router import router
        paths = [r.path for r in router.routes]
        assert "/dashboard/ws" in paths
