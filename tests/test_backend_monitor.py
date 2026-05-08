"""Tests for Phase 21 (Block D1): Backend Reachability Monitor."""
from __future__ import annotations

import sys
import time
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.backend_monitor import (
    BackendMonitor,
    can_handle_without_backend,
    get_monitor,
    start_monitor,
    stop_monitor,
)


# ---------------------------------------------------------------------------
# BackendMonitor state machine tests
# ---------------------------------------------------------------------------

class TestBackendMonitorStateMachine:
    def setup_method(self):
        self.notifications = []
        self.monitor = BackendMonitor(
            backend_url="http://127.0.0.1:9999",  # nothing there
            notify_callback=lambda msg, level: self.notifications.append((msg, level)),
            check_interval=60,
            retry_interval=0.1,
            down_warn_after=0.3,  # very short for testing
        )

    def teardown_method(self):
        self.monitor.stop()

    def test_initial_status_is_unknown(self):
        assert self.monitor.current_status() is None

    def test_starts_and_stops(self):
        self.monitor.start()
        time.sleep(0.2)
        assert self.monitor.is_active()
        self.monitor.stop()
        assert not self.monitor.is_active()

    def test_detects_backend_down(self):
        self.monitor.start()
        time.sleep(0.5)
        self.monitor.stop()
        assert self.monitor.current_status() is False

    def test_notifies_on_newly_down(self):
        # Patch _check_once to first return True, then False
        calls = [True, False]
        self.monitor._is_up = True  # pretend it was up
        self.monitor._check_once = lambda: False
        self.monitor._handle_state_change(False)
        assert any("недоступен" in msg.lower() or "down" in msg.lower() for msg, _ in self.notifications)

    def test_notifies_on_recovery(self):
        self.monitor._is_up = False  # pretend it was down
        self.monitor._down_since = time.time() - 10
        self.monitor._check_once = lambda: True
        self.monitor._handle_state_change(True)
        assert any("восстановлен" in msg.lower() for msg, _ in self.notifications)

    def test_no_notification_if_already_down(self):
        self.monitor._is_up = False
        self.monitor._down_since = time.time()
        self.monitor._warned_long_down = False
        initial_count = len(self.notifications)
        self.monitor._handle_state_change(False)
        assert len(self.notifications) == initial_count

    def test_long_down_triggers_warn_notification(self):
        self.monitor._is_up = False
        self.monitor._down_since = time.time() - 400  # > 300s
        self.monitor._warned_long_down = False
        self.monitor._handle_state_change(False)
        assert any("5+" in msg or "недоступен" in msg for msg, level in self.notifications if level == "warn")

    def test_long_down_warn_fires_once(self):
        self.monitor._is_up = False
        self.monitor._down_since = time.time() - 400
        self.monitor._warned_long_down = False
        self.monitor._handle_state_change(False)
        count1 = len([n for n in self.notifications if "5+" in n[0]])
        self.monitor._handle_state_change(False)
        count2 = len([n for n in self.notifications if "5+" in n[0]])
        assert count1 == count2  # second call didn't add another

    def test_recovery_resets_warned_flag(self):
        self.monitor._is_up = False
        self.monitor._down_since = time.time() - 10
        self.monitor._warned_long_down = True
        self.monitor._handle_state_change(True)
        assert self.monitor._warned_long_down is False

    def test_notify_includes_restart_hint_when_long_down(self):
        self.monitor._is_up = False
        self.monitor._down_since = time.time() - 400
        self.monitor._warned_long_down = False
        self.monitor._handle_state_change(False)
        assert any("uvicorn" in msg.lower() or "запусти" in msg.lower() for msg, _ in self.notifications)


# ---------------------------------------------------------------------------
# can_handle_without_backend
# ---------------------------------------------------------------------------

class TestGracefulDegradation:
    def test_identity_works_without_backend(self):
        assert can_handle_without_backend("identity") is True

    def test_greeting_works_without_backend(self):
        assert can_handle_without_backend("greeting") is True

    def test_small_talk_works_without_backend(self):
        assert can_handle_without_backend("small_talk") is True

    def test_research_needs_backend(self):
        assert can_handle_without_backend("research") is False

    def test_table_needs_backend(self):
        assert can_handle_without_backend("table") is False

    def test_compound_task_needs_backend(self):
        assert can_handle_without_backend("compound_task") is False


# ---------------------------------------------------------------------------
# Global singleton tests
# ---------------------------------------------------------------------------

class TestGlobalMonitor:
    def teardown_method(self):
        stop_monitor()

    def test_start_returns_monitor(self):
        notifications = []
        m = start_monitor("http://127.0.0.1:9999", lambda msg, lvl: notifications.append(msg))
        assert m is not None
        assert m.is_active()
        stop_monitor()

    def test_get_monitor_returns_started(self):
        notifications = []
        start_monitor("http://127.0.0.1:9999", lambda msg, lvl: None)
        assert get_monitor() is not None
        stop_monitor()
        assert get_monitor() is None

    def test_double_start_returns_same(self):
        notifications = []
        m1 = start_monitor("http://127.0.0.1:9999", lambda msg, lvl: None)
        m2 = start_monitor("http://127.0.0.1:9999", lambda msg, lvl: None)
        assert m1 is m2
        stop_monitor()
