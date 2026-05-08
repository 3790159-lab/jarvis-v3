"""Phase H9.1: Single-instance guard via PID lockfile."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _import_check_fn(tmp_pid: Path):
    """Import and re-point _PID_FILE to tmp path."""
    import tools.jarvis_smart_telegram_control as mod
    original = mod._PID_FILE
    mod._PID_FILE = tmp_pid
    return mod, original


# ---------------------------------------------------------------------------


def test_check_single_instance_function_exists():
    import tools.jarvis_smart_telegram_control as mod
    assert callable(mod._check_single_instance)


def test_pid_file_constant_defined():
    import tools.jarvis_smart_telegram_control as mod
    assert hasattr(mod, "_PID_FILE")
    assert isinstance(mod._PID_FILE, Path)


def test_pid_file_path_is_state_bot_pid():
    import tools.jarvis_smart_telegram_control as mod
    assert str(mod._PID_FILE).replace("\\", "/").endswith("state/bot.pid")


def test_check_single_instance_no_existing_pid(tmp_path):
    import tools.jarvis_smart_telegram_control as mod
    pid_file = tmp_path / "bot.pid"
    original = mod._PID_FILE
    mod._PID_FILE = pid_file
    try:
        result = mod._check_single_instance()
        assert result is True
        assert pid_file.exists()
    finally:
        mod._PID_FILE = original
        if pid_file.exists():
            pid_file.unlink()


def test_check_single_instance_creates_pid_with_current_pid(tmp_path):
    import tools.jarvis_smart_telegram_control as mod
    pid_file = tmp_path / "bot.pid"
    original = mod._PID_FILE
    mod._PID_FILE = pid_file
    try:
        mod._check_single_instance()
        stored_pid = int(pid_file.read_text(encoding="utf-8").strip())
        assert stored_pid == os.getpid()
    finally:
        mod._PID_FILE = original
        if pid_file.exists():
            pid_file.unlink()


def test_check_single_instance_stale_pid_returns_true(tmp_path):
    """Non-existent PID → stale lockfile → should allow start."""
    import tools.jarvis_smart_telegram_control as mod
    pid_file = tmp_path / "bot.pid"
    pid_file.write_text("9999999", encoding="utf-8")  # unlikely PID
    original = mod._PID_FILE
    mod._PID_FILE = pid_file

    # On Windows mock tasklist to return nothing; on Unix mock os.kill to raise OSError
    if sys.platform == "win32":
        fake_result = MagicMock()
        fake_result.stdout = "INFO: No tasks are running which match the specified criteria."
        with patch("subprocess.run", return_value=fake_result):
            mod._PID_FILE = original  # restore before patch scope
            mod._PID_FILE = pid_file
            try:
                result = mod._check_single_instance()
                assert result is True
            finally:
                mod._PID_FILE = original
                if pid_file.exists():
                    pid_file.unlink()
    else:
        with patch("os.kill", side_effect=OSError):
            try:
                result = mod._check_single_instance()
                assert result is True
            finally:
                mod._PID_FILE = original
                if pid_file.exists():
                    pid_file.unlink()


def test_check_single_instance_live_process_returns_false(tmp_path):
    """If PID exists and process is running → should return False."""
    import tools.jarvis_smart_telegram_control as mod
    pid_file = tmp_path / "bot.pid"
    pid_file.write_text(str(os.getpid()), encoding="utf-8")  # our own PID
    original = mod._PID_FILE
    mod._PID_FILE = pid_file

    if sys.platform == "win32":
        fake_result = MagicMock()
        fake_result.stdout = f"python.exe  {os.getpid()} "
        with patch("subprocess.run", return_value=fake_result):
            try:
                result = mod._check_single_instance()
                assert result is False
            finally:
                mod._PID_FILE = original
    else:
        try:
            result = mod._check_single_instance()
            assert result is False
        finally:
            mod._PID_FILE = original


def test_check_single_instance_corrupted_pid_returns_true(tmp_path):
    """Corrupted PID file (not a number) → treated as stale → allow start."""
    import tools.jarvis_smart_telegram_control as mod
    pid_file = tmp_path / "bot.pid"
    pid_file.write_text("not_a_number", encoding="utf-8")
    original = mod._PID_FILE
    mod._PID_FILE = pid_file
    try:
        result = mod._check_single_instance()
        assert result is True
    finally:
        mod._PID_FILE = original
        if pid_file.exists():
            pid_file.unlink()


def test_main_calls_check_single_instance():
    """main() exits early when _check_single_instance returns False."""
    import tools.jarvis_smart_telegram_control as mod
    calls = []

    def _fake_check():
        calls.append(1)
        return False  # simulate duplicate instance

    with patch.object(mod, "_check_single_instance", side_effect=_fake_check):
        try:
            mod.main()
        except SystemExit as e:
            assert e.code == 1
        else:
            assert False, "Expected SystemExit(1)"

    assert len(calls) == 1
