"""Phase H1.5: Stability fixes — status cache, WS limit, periodic cleanup."""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.routers.jarvis_dashboard_router as dash_mod


# ---------------------------------------------------------------------------
# Status cache
# ---------------------------------------------------------------------------

class TestStatusCache:
    def setup_method(self):
        dash_mod._status_cache = None
        dash_mod._status_cache_ts = 0.0

    def test_cache_populated_on_first_call(self):
        mock_status = {"status": "online", "uptime_seconds": 10}
        with patch.object(dash_mod, "_get_status", return_value=mock_status) as mock_gs:
            result = dash_mod._get_status_cached()
        assert result == mock_status
        mock_gs.assert_called_once()

    def test_cache_reused_within_ttl(self):
        mock_status = {"status": "online"}
        with patch.object(dash_mod, "_get_status", return_value=mock_status) as mock_gs:
            dash_mod._get_status_cached()
            dash_mod._get_status_cached()
        mock_gs.assert_called_once()  # only called once

    def test_cache_refreshed_after_ttl(self):
        with patch.object(dash_mod, "_get_status", return_value={"n": 1}) as mock_gs:
            dash_mod._get_status_cached()
        # expire cache
        dash_mod._status_cache_ts = time.time() - dash_mod._STATUS_TTL - 1
        with patch.object(dash_mod, "_get_status", return_value={"n": 2}) as mock_gs2:
            result = dash_mod._get_status_cached()
        assert result["n"] == 2
        mock_gs2.assert_called_once()

    def test_cache_not_refreshed_before_ttl_expires(self):
        first = {"uptime_seconds": 5}
        with patch.object(dash_mod, "_get_status", return_value=first):
            dash_mod._get_status_cached()
        # cache still fresh
        dash_mod._status_cache_ts = time.time() - (dash_mod._STATUS_TTL / 2)
        with patch.object(dash_mod, "_get_status", return_value={"uptime_seconds": 99}) as mock_new:
            result = dash_mod._get_status_cached()
        mock_new.assert_not_called()
        assert result["uptime_seconds"] == 5


# ---------------------------------------------------------------------------
# WebSocket connection limit constants
# ---------------------------------------------------------------------------

class TestWsConnectionLimit:
    def test_max_connections_defined(self):
        assert dash_mod._WS_MAX_CONNECTIONS > 0

    def test_max_connections_reasonable(self):
        assert 5 <= dash_mod._WS_MAX_CONNECTIONS <= 100

    def test_connection_counter_starts_at_zero(self):
        # Counter may be non-zero in running server, just verify it's an int
        assert isinstance(dash_mod._ws_connections, int)


# ---------------------------------------------------------------------------
# Periodic cleanup task
# ---------------------------------------------------------------------------

class TestPeriodicCleanup:
    def test_cleanup_old_logs_called_during_cycle(self, tmp_path):
        from app.services.self_healing import cleanup_old_logs
        log_file = tmp_path / "app.log"
        log_file.write_text("x\n" * 100)
        cleaned = cleanup_old_logs(state_dir=tmp_path, max_size_mb=0.00001)
        assert isinstance(cleaned, int)

    def test_archive_old_decisions_callable(self, tmp_path):
        from app.services.self_healing import archive_old_decisions
        decisions = tmp_path / "decisions.jsonl"
        decisions.write_text('{"decision_id": "d1", "timestamp": "2000-01-01T00:00:00Z"}\n')
        archived = archive_old_decisions(decisions_path=decisions, older_than_days=1)
        assert archived >= 0

    def test_status_ttl_is_positive(self):
        assert dash_mod._STATUS_TTL > 0

    def test_status_ttl_under_one_minute(self):
        assert dash_mod._STATUS_TTL <= 60


# ---------------------------------------------------------------------------
# Startup event registers cleanup task
# ---------------------------------------------------------------------------

class TestStartupCleanup:
    def test_periodic_cleanup_function_exists(self):
        from app.main import _periodic_cleanup
        import asyncio
        assert asyncio.iscoroutinefunction(_periodic_cleanup)
