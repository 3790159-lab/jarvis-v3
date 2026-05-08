"""Phase 19 (Block D1): Cowork Watchdog — filesystem watcher for cowork_outbox.

Runs as a background daemon thread. Detects new JSON files in cowork_outbox
and calls the registered delivery callback with the parsed result.

Auto-recovers if the watched folder is deleted/recreated.
Falls back to polling if watchdog is not available.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent.parent
_OUTBOX = ROOT / "state" / "cowork_outbox"
_OUTBOX.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Delivery callback type
# ---------------------------------------------------------------------------
DeliverCallback = Callable[[Dict[str, Any]], None]

# ---------------------------------------------------------------------------
# Safety limits
# ---------------------------------------------------------------------------
_MAX_TASK_SIZE_BYTES = 100 * 1024  # 100 KB
_BLOCKED_PATH_FRAGMENTS = ["/etc/", "\\etc\\", "/proc/", "system32", "passwd", "shadow"]


def _is_safe_instruction(instruction: str) -> bool:
    """Reject instructions that attempt filesystem injection."""
    low = instruction.lower()
    for fragment in _BLOCKED_PATH_FRAGMENTS:
        if fragment.lower() in low:
            return False
    return True


def sanitize_task(task: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and sanitize a task dict before sending to Cowork."""
    instruction = str(task.get("instruction", ""))
    if not instruction.strip():
        raise ValueError("Task instruction is empty")
    if len(json.dumps(task, ensure_ascii=False).encode()) > _MAX_TASK_SIZE_BYTES:
        raise ValueError(f"Task exceeds size limit ({_MAX_TASK_SIZE_BYTES // 1024}KB)")
    if not _is_safe_instruction(instruction):
        raise ValueError("Task instruction contains blocked path fragment")
    return task


# ---------------------------------------------------------------------------
# Watchdog-based implementation
# ---------------------------------------------------------------------------

_watchdog_available = False
try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
    _watchdog_available = True
except ImportError:
    pass


if _watchdog_available:
    class _OutboxEventHandler(FileSystemEventHandler):  # type: ignore[misc]
        def __init__(self, callback: DeliverCallback) -> None:
            super().__init__()
            self._callback = callback

        def on_created(self, event: Any) -> None:  # type: ignore[override]
            if event.is_directory:
                return
            path = Path(event.src_path)
            if path.suffix != ".json":
                return
            # Small delay to ensure file is fully written
            time.sleep(0.15)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                logger.info("CoworkWatcher: new result %s", path.name)
                self._callback(data)
            except Exception as exc:
                logger.warning("CoworkWatcher: failed to parse %s: %s", path.name, exc)


class CoworkWatcher:
    """Background watcher for cowork_outbox. Call start() once at bot startup."""

    def __init__(
        self,
        callback: DeliverCallback,
        timeout_sec: int = 300,
        poll_interval: float = 3.0,
        outbox_dir: Optional[Path] = None,
    ) -> None:
        self._callback = callback
        self._timeout_sec = timeout_sec
        self._poll_interval = poll_interval
        self._outbox = outbox_dir or _OUTBOX
        self._active = False
        self._thread: Optional[threading.Thread] = None
        self._observer: Optional[Any] = None  # watchdog Observer
        self._known_tasks: Dict[str, float] = {}  # task_id → submitted_at (for timeout tracking)
        self._lock = threading.Lock()

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def start(self) -> None:
        if self._active:
            return
        self._active = True
        if _watchdog_available:
            self._start_watchdog()
        else:
            self._start_polling()
        logger.info("CoworkWatcher started (mode=%s)", "watchdog" if _watchdog_available else "polling")

    def stop(self) -> None:
        self._active = False
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=3)
            except Exception:
                pass
            self._observer = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("CoworkWatcher stopped")

    def is_active(self) -> bool:
        return self._active

    def track_task(self, task_id: str) -> None:
        """Register task_id for timeout monitoring."""
        with self._lock:
            self._known_tasks[task_id] = time.time()

    def untrack_task(self, task_id: str) -> None:
        with self._lock:
            self._known_tasks.pop(task_id, None)

    # -----------------------------------------------------------------------
    # Watchdog mode
    # -----------------------------------------------------------------------

    def _start_watchdog(self) -> None:
        self._outbox.mkdir(parents=True, exist_ok=True)
        handler = _OutboxEventHandler(self._callback)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._outbox), recursive=False)
        self._observer.start()
        # Start timeout monitor in background thread
        self._thread = threading.Thread(target=self._timeout_monitor_loop, daemon=True, name="cowork-timeout")
        self._thread.start()

    # -----------------------------------------------------------------------
    # Polling mode (fallback when watchdog unavailable)
    # -----------------------------------------------------------------------

    def _start_polling(self) -> None:
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="cowork-poll")
        self._thread.start()

    def _poll_loop(self) -> None:
        seen: set = set()
        while self._active:
            try:
                self._outbox.mkdir(parents=True, exist_ok=True)
                for f in self._outbox.glob("*.json"):
                    if f.name not in seen:
                        seen.add(f.name)
                        try:
                            data = json.loads(f.read_text(encoding="utf-8"))
                            logger.info("CoworkWatcher(poll): new result %s", f.name)
                            self._callback(data)
                        except Exception as exc:
                            logger.warning("CoworkWatcher(poll): error reading %s: %s", f.name, exc)
                self._check_timeouts()
            except Exception as exc:
                logger.warning("CoworkWatcher(poll): loop error: %s", exc)
            time.sleep(self._poll_interval)

    def _timeout_monitor_loop(self) -> None:
        while self._active:
            self._check_timeouts()
            time.sleep(self._poll_interval)

    def _check_timeouts(self) -> None:
        now = time.time()
        expired = []
        with self._lock:
            for task_id, submitted_at in list(self._known_tasks.items()):
                if now - submitted_at > self._timeout_sec:
                    expired.append(task_id)
        for task_id in expired:
            with self._lock:
                self._known_tasks.pop(task_id, None)
            timeout_result = {
                "task_id": task_id,
                "status": "timeout",
                "result": f"⚠️ Cowork не ответил за {self._timeout_sec}с. Проверь Claude Desktop.",
                "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            try:
                self._callback(timeout_result)
            except Exception as exc:
                logger.warning("CoworkWatcher: timeout callback error: %s", exc)


# ---------------------------------------------------------------------------
# Global singleton (created lazily by the bot)
# ---------------------------------------------------------------------------

_global_watcher: Optional[CoworkWatcher] = None


def get_watcher() -> Optional[CoworkWatcher]:
    return _global_watcher


def start_watcher(callback: DeliverCallback, timeout_sec: int = 300) -> CoworkWatcher:
    global _global_watcher
    if _global_watcher and _global_watcher.is_active():
        return _global_watcher
    _global_watcher = CoworkWatcher(callback=callback, timeout_sec=timeout_sec)
    _global_watcher.start()
    return _global_watcher


def stop_watcher() -> None:
    global _global_watcher
    if _global_watcher:
        _global_watcher.stop()
        _global_watcher = None
