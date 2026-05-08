"""Phase 21 (Block D1): Backend Reachability Monitor.

Runs as a background daemon thread. Checks backend /health every 30 seconds.
On state transitions (up→down, down→up) notifies via callback.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

NotifyCallback = Callable[[str, str], None]  # (message, level: "info"|"warn") → None

_DEFAULT_CHECK_INTERVAL = 30.0
_RETRY_INTERVAL = 5.0
_DOWN_WARN_AFTER = 300.0  # 5 minutes


class BackendMonitor:
    """Monitors backend health. Calls notify_callback on state changes."""

    def __init__(
        self,
        backend_url: str,
        notify_callback: NotifyCallback,
        check_interval: float = _DEFAULT_CHECK_INTERVAL,
        retry_interval: float = _RETRY_INTERVAL,
        down_warn_after: float = _DOWN_WARN_AFTER,
    ) -> None:
        self._url = backend_url.rstrip("/") + "/health"
        self._notify = notify_callback
        self._check_interval = check_interval
        self._retry_interval = retry_interval
        self._down_warn_after = down_warn_after
        self._thread: Optional[threading.Thread] = None
        self._active = False
        self._is_up: Optional[bool] = None  # None = unknown yet
        self._down_since: Optional[float] = None
        self._warned_long_down = False
        self._lock = threading.Lock()

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def start(self) -> None:
        if self._active:
            return
        self._active = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="backend-monitor")
        self._thread.start()
        logger.info("BackendMonitor started for %s", self._url)

    def stop(self) -> None:
        self._active = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def is_active(self) -> bool:
        return self._active

    def current_status(self) -> Optional[bool]:
        """Return True=up, False=down, None=unknown."""
        return self._is_up

    # -----------------------------------------------------------------------
    # Internal loop
    # -----------------------------------------------------------------------

    def _run(self) -> None:
        while self._active:
            up = self._check_once()
            self._handle_state_change(up)
            interval = self._check_interval if up else self._retry_interval
            # Sleep in small slices so stop() takes effect quickly
            elapsed = 0.0
            while elapsed < interval and self._active:
                time.sleep(min(1.0, interval - elapsed))
                elapsed += 1.0

    def _check_once(self) -> bool:
        import urllib.request
        try:
            req = urllib.request.urlopen(self._url, timeout=5)
            return req.status < 500
        except Exception:
            return False

    def _handle_state_change(self, up: bool) -> None:
        with self._lock:
            prev = self._is_up
            self._is_up = up

            if up:
                if prev is False:
                    # Recovered
                    self._down_since = None
                    self._warned_long_down = False
                    self._notify("✅ Backend восстановлен.", "info")
                    logger.info("BackendMonitor: backend RECOVERED")
                return

            # Backend is down
            now = time.time()
            if prev is not False:
                # Newly down
                self._down_since = now
                self._warned_long_down = False
                if prev is not None:
                    self._notify("🔴 Backend недоступен. Попробую снова...", "warn")
                    logger.warning("BackendMonitor: backend DOWN")
            elif (
                self._down_since is not None
                and not self._warned_long_down
                and now - self._down_since > self._down_warn_after
            ):
                self._warned_long_down = True
                self._notify(
                    "⚠️ Backend недоступен уже 5+ минут. "
                    "Запусти backend заново:\n"
                    "  .venv\\Scripts\\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010",
                    "warn",
                )
                logger.warning("BackendMonitor: backend DOWN > 5min")


# ---------------------------------------------------------------------------
# Graceful degradation helpers
# ---------------------------------------------------------------------------

def can_handle_without_backend(intent: str) -> bool:
    """Return True if this intent works without a backend connection."""
    local_intents = {
        "identity", "greeting", "small_talk", "capability_query",
        "file_parse_local",  # Phase 13 local parsing
    }
    return intent in local_intents


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_global_monitor: Optional[BackendMonitor] = None


def get_monitor() -> Optional[BackendMonitor]:
    return _global_monitor


def start_monitor(backend_url: str, notify_callback: NotifyCallback) -> BackendMonitor:
    global _global_monitor
    if _global_monitor and _global_monitor.is_active():
        return _global_monitor
    _global_monitor = BackendMonitor(backend_url=backend_url, notify_callback=notify_callback)
    _global_monitor.start()
    return _global_monitor


def stop_monitor() -> None:
    global _global_monitor
    if _global_monitor:
        _global_monitor.stop()
        _global_monitor = None
