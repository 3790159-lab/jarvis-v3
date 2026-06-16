"""Phase 35: Tests for system_watchdog — health checks and auto-recovery."""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.system_watchdog import (
    check_backend,
    check_disk_space,
    check_memory,
    cleanup_old_logs,
    run_watchdog_cycle,
    send_telegram_alert,
    restart_service,
    restart_bot_if_dead,
    check_bot_alive,
    heartbeat_check_interval_sec,
)


# ---------------------------------------------------------------------------
# check_backend
# ---------------------------------------------------------------------------

class TestCheckBackend:
    def test_returns_ok_when_backend_responds(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status": "ok"}'
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = check_backend()
        assert result["ok"] is True

    def test_returns_not_ok_on_connection_error(self):
        with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError("refused")):
            result = check_backend()
        assert result["ok"] is False
        assert len(result["detail"]) > 0

    def test_returns_not_ok_on_timeout(self):
        import socket
        with patch("urllib.request.urlopen", side_effect=socket.timeout("timed out")):
            result = check_backend()
        assert result["ok"] is False

    def test_result_has_required_keys(self):
        with patch("urllib.request.urlopen", side_effect=Exception("err")):
            result = check_backend()
        assert "ok" in result
        assert "detail" in result


# ---------------------------------------------------------------------------
# check_disk_space
# ---------------------------------------------------------------------------

class TestCheckDiskSpace:
    def test_ok_when_enough_space(self):
        with patch("shutil.disk_usage", return_value=(100e9, 50e9, 10e9)):  # 10GB free
            result = check_disk_space()
        assert result["ok"] is True
        assert result["free_gb"] >= 1.0

    def test_not_ok_when_low_space(self):
        with patch("shutil.disk_usage", return_value=(100e9, 99.5e9, 0.5e9)):  # 0.5GB free
            result = check_disk_space()
        assert result["ok"] is False

    def test_result_has_free_gb(self):
        with patch("shutil.disk_usage", return_value=(100e9, 50e9, 5e9)):
            result = check_disk_space()
        assert "free_gb" in result
        assert result["free_gb"] == pytest.approx(5e9 / 1024**3, abs=0.1)

    def test_no_crash_on_exception(self):
        with patch("shutil.disk_usage", side_effect=Exception("disk error")):
            result = check_disk_space()
        assert "ok" in result


# ---------------------------------------------------------------------------
# check_memory
# ---------------------------------------------------------------------------

class TestCheckMemory:
    def test_ok_when_low_memory_usage(self):
        mock_psutil = MagicMock()
        mock_mem = MagicMock()
        mock_mem.percent = 50.0
        mock_psutil.virtual_memory.return_value = mock_mem
        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            result = check_memory()
        assert result["ok"] is True

    def test_not_ok_when_high_memory_usage(self):
        mock_psutil = MagicMock()
        mock_mem = MagicMock()
        mock_mem.percent = 95.0
        mock_psutil.virtual_memory.return_value = mock_mem
        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            result = check_memory()
        assert result["ok"] is False

    def test_ok_when_psutil_not_available(self):
        with patch.dict("sys.modules", {"psutil": None}):
            result = check_memory()
        assert result["ok"] is True


# ---------------------------------------------------------------------------
# cleanup_old_logs
# ---------------------------------------------------------------------------

class TestCleanupOldLogs:
    def test_truncates_large_log(self, tmp_path):
        log = tmp_path / "backend.log"
        # Write 51MB of content (simulated by many lines)
        content = "\n".join([f"log line {i}" for i in range(10000)])
        log.write_text(content, encoding="utf-8")

        # Patch stat to report large size
        original_stat = Path.stat
        def fake_stat(self):
            s = original_stat(self)
            if self.name.endswith(".log"):
                import os
                return os.stat_result((s.st_mode, s.st_ino, s.st_dev, s.st_nlink,
                                       s.st_uid, s.st_gid, 55 * 1024 * 1024,
                                       s.st_atime, s.st_mtime, s.st_ctime))
            return s

        with patch.object(Path, "stat", fake_stat):
            cleaned = cleanup_old_logs(tmp_path, max_log_mb=50)
        assert cleaned == 1

    def test_no_cleanup_when_small(self, tmp_path):
        log = tmp_path / "bot.log"
        log.write_text("small log content\n" * 10, encoding="utf-8")
        cleaned = cleanup_old_logs(tmp_path, max_log_mb=50)
        assert cleaned == 0

    def test_keeps_last_1000_lines(self, tmp_path):
        log = tmp_path / "test.log"
        lines = [f"line {i}" for i in range(5000)]
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")

        original_stat = Path.stat
        def fake_stat(self):
            s = original_stat(self)
            if self.name.endswith(".log"):
                import os
                return os.stat_result((s.st_mode, s.st_ino, s.st_dev, s.st_nlink,
                                       s.st_uid, s.st_gid, 55 * 1024 * 1024,
                                       s.st_atime, s.st_mtime, s.st_ctime))
            return s

        with patch.object(Path, "stat", fake_stat):
            cleanup_old_logs(tmp_path, max_log_mb=50)

        result_lines = log.read_text(encoding="utf-8").strip().splitlines()
        assert len(result_lines) == 1000
        assert result_lines[-1] == "line 4999"  # last line preserved

    def test_no_crash_on_empty_dir(self, tmp_path):
        cleaned = cleanup_old_logs(tmp_path / "nonexistent")
        assert cleaned == 0


# ---------------------------------------------------------------------------
# send_telegram_alert
# ---------------------------------------------------------------------------

class TestSendTelegramAlert:
    def test_returns_false_without_credentials(self):
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_ALLOWED_CHAT_ID": ""}):
            from importlib import reload
            import app.services.system_watchdog as wdog
            reload(wdog)
            # Patch module-level vars
            orig_bot = wdog.BOT_TOKEN
            orig_chat = wdog.ADMIN_CHAT_ID
            wdog.BOT_TOKEN = ""
            wdog.ADMIN_CHAT_ID = ""
            result = wdog.send_telegram_alert("test")
            wdog.BOT_TOKEN = orig_bot
            wdog.ADMIN_CHAT_ID = orig_chat
        assert result is False

    def test_no_crash_on_network_error(self):
        import app.services.system_watchdog as wdog
        orig_bot = wdog.BOT_TOKEN
        orig_chat = wdog.ADMIN_CHAT_ID
        wdog.BOT_TOKEN = "fake_token"
        wdog.ADMIN_CHAT_ID = "123"
        with patch("urllib.request.urlopen", side_effect=Exception("network")):
            result = wdog.send_telegram_alert("test message")
        wdog.BOT_TOKEN = orig_bot
        wdog.ADMIN_CHAT_ID = orig_chat
        assert result is False


# ---------------------------------------------------------------------------
# run_watchdog_cycle
# ---------------------------------------------------------------------------

class TestRunWatchdogCycle:
    def test_returns_result_dict(self):
        with patch("app.services.system_watchdog.check_backend", return_value={"ok": True, "detail": "ok"}):
            with patch("app.services.system_watchdog.check_disk_space", return_value={"ok": True, "free_gb": 10, "detail": "10GB"}):
                with patch("app.services.system_watchdog.check_memory", return_value={"ok": True, "percent": 50, "detail": "50%"}):
                    result = run_watchdog_cycle()
        assert "backend" in result
        assert "disk" in result
        assert "memory" in result
        assert "actions_taken" in result
        assert "timestamp" in result

    def test_no_actions_when_healthy(self):
        with patch("app.services.system_watchdog.check_backend", return_value={"ok": True, "detail": "ok"}):
            with patch("app.services.system_watchdog.check_disk_space", return_value={"ok": True, "free_gb": 10, "detail": "10GB"}):
                with patch("app.services.system_watchdog.check_memory", return_value={"ok": True, "percent": 50, "detail": "50%"}):
                    result = run_watchdog_cycle()
        assert result["actions_taken"] == []

    def test_restart_attempted_on_backend_failure(self):
        restart_calls = []
        with patch("app.services.system_watchdog.check_backend", return_value={"ok": False, "detail": "refused"}):
            with patch("app.services.system_watchdog.check_disk_space", return_value={"ok": True, "free_gb": 10, "detail": "10GB"}):
                with patch("app.services.system_watchdog.check_memory", return_value={"ok": True, "percent": 50, "detail": "50%"}):
                    with patch("app.services.system_watchdog.restart_service", side_effect=lambda name: restart_calls.append(name) or True):
                        with patch("app.services.system_watchdog.send_telegram_alert"):
                            with patch("time.sleep"):
                                result = run_watchdog_cycle()
        assert "JarvisBackend" in restart_calls

    def test_cleanup_attempted_on_low_disk(self, tmp_path):
        cleanup_calls = []
        with patch("app.services.system_watchdog.check_backend", return_value={"ok": True, "detail": "ok"}):
            with patch("app.services.system_watchdog.check_disk_space", return_value={"ok": False, "free_gb": 0.5, "detail": "0.5GB free"}):
                with patch("app.services.system_watchdog.check_memory", return_value={"ok": True, "percent": 50, "detail": "50%"}):
                    with patch("app.services.system_watchdog.cleanup_old_logs", return_value=2) as mock_clean:
                        with patch("app.services.system_watchdog.send_telegram_alert"):
                            result = run_watchdog_cycle()
        mock_clean.assert_called_once()


# ---------------------------------------------------------------------------
# restart_service
# ---------------------------------------------------------------------------

class TestRestartService:
    def test_skips_on_non_windows(self):
        with patch("sys.platform", "linux"):
            result = restart_service("JarvisBot")
        assert result is False

    def test_returns_true_on_success(self):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        with patch("sys.platform", "win32"):
            with patch("subprocess.run", return_value=mock_proc):
                result = restart_service("JarvisBot")
        assert result is True

    def test_returns_false_on_failure(self):
        with patch("sys.platform", "win32"):
            with patch("subprocess.run", side_effect=Exception("access denied")):
                result = restart_service("JarvisBot")
        assert result is False


# ---------------------------------------------------------------------------
# restart_bot_if_dead — STEP 1: env stop-gap flag
# ---------------------------------------------------------------------------

class TestRestartBotDisabledViaEnv:
    def test_returns_false_when_disabled_via_env(self):
        """WATCHDOG_DISABLE_BOT_RESTART=1 → no restart attempted, returns False.

        Set up conditions that would otherwise trigger a restart (past grace
        period + stale heartbeat) to prove the flag is what short-circuits.
        """
        import app.services.system_watchdog as wdog
        with patch.dict("os.environ", {"WATCHDOG_DISABLE_BOT_RESTART": "1"}):
            with patch.object(wdog, "_module_started_at", 0.0):  # far past grace
                with patch.object(wdog, "check_bot_alive", return_value=False):  # stale
                    with patch("subprocess.Popen") as mock_popen:
                        result = wdog.restart_bot_if_dead()
        assert result is False
        mock_popen.assert_not_called()


# ---------------------------------------------------------------------------
# restart_bot_if_dead — STEP 2: busy-aware + -BotOnly + alert throttle
# ---------------------------------------------------------------------------

class TestRestartBotBusyAware:
    def test_skips_restart_while_swap_active(self):
        import app.services.system_watchdog as wdog
        wdog._last_restart_alert_at = 0.0
        with patch.object(wdog, "_module_started_at", 0.0):
            with patch.object(wdog, "check_bot_alive", return_value=False):
                with patch.object(wdog, "is_swap_active", return_value=True):
                    with patch("subprocess.Popen") as mock_popen:
                        result = wdog.restart_bot_if_dead()
        assert result is False
        mock_popen.assert_not_called()

    def test_restarts_with_bot_only_flag_when_idle_and_stale(self):
        import app.services.system_watchdog as wdog
        wdog._last_restart_alert_at = 0.0
        with patch.object(wdog, "_module_started_at", 0.0):
            with patch.object(wdog, "check_bot_alive", return_value=False):
                with patch.object(wdog, "is_swap_active", return_value=False):
                    with patch.object(wdog, "send_telegram_alert"):
                        with patch("subprocess.Popen") as mock_popen:
                            result = wdog.restart_bot_if_dead()
        assert result is True
        mock_popen.assert_called_once()
        args = mock_popen.call_args[0][0]
        assert "-BotOnly" in args

    def test_alert_is_throttled_across_consecutive_restarts(self):
        import app.services.system_watchdog as wdog
        wdog._last_restart_alert_at = 0.0
        with patch.object(wdog, "_module_started_at", 0.0):
            with patch.object(wdog, "check_bot_alive", return_value=False):
                with patch.object(wdog, "is_swap_active", return_value=False):
                    with patch("subprocess.Popen"):
                        with patch.object(wdog, "send_telegram_alert") as mock_alert:
                            wdog.restart_bot_if_dead()
                            wdog.restart_bot_if_dead()
                            wdog.restart_bot_if_dead()
        # back-to-back restarts within the throttle window → one alert only
        assert mock_alert.call_count == 1


# ---------------------------------------------------------------------------
# STEP 3: env-configurable heartbeat staleness + check interval
# ---------------------------------------------------------------------------

class TestHeartbeatStaleThreshold:
    def test_default_threshold_is_300(self, tmp_path, monkeypatch):
        monkeypatch.delenv("WATCHDOG_HEARTBEAT_STALE_SEC", raising=False)
        hb = tmp_path / "bot_heartbeat.txt"
        hb.write_text(str(int(time.time()) - 200), encoding="utf-8")  # 200s ago
        # 200s would be stale under the old hardcoded 90s; fresh under the new 300s default
        assert check_bot_alive(heartbeat_file=hb) is True

    def test_fresh_within_env_threshold(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WATCHDOG_HEARTBEAT_STALE_SEC", "600")
        hb = tmp_path / "bot_heartbeat.txt"
        hb.write_text(str(int(time.time()) - 400), encoding="utf-8")  # 400s ago
        assert check_bot_alive(heartbeat_file=hb) is True

    def test_stale_beyond_env_threshold(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WATCHDOG_HEARTBEAT_STALE_SEC", "300")
        hb = tmp_path / "bot_heartbeat.txt"
        hb.write_text(str(int(time.time()) - 400), encoding="utf-8")  # 400s ago
        assert check_bot_alive(heartbeat_file=hb) is False

    def test_missing_file_is_not_alive(self, tmp_path):
        assert check_bot_alive(heartbeat_file=tmp_path / "nope.txt") is False


class TestCheckIntervalConfig:
    def test_default_interval_is_60(self, monkeypatch):
        monkeypatch.delenv("WATCHDOG_CHECK_INTERVAL_SEC", raising=False)
        assert heartbeat_check_interval_sec() == 60

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("WATCHDOG_CHECK_INTERVAL_SEC", "120")
        assert heartbeat_check_interval_sec() == 120

    def test_invalid_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("WATCHDOG_CHECK_INTERVAL_SEC", "not-a-number")
        assert heartbeat_check_interval_sec() == 60


# Need pytest for approx
import pytest
