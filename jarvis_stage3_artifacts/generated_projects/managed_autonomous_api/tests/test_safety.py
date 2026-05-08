import pytest

from app.safety import assert_safe_shell_command


def test_safe_shell_allows_echo():
    assert_safe_shell_command("echo hello")


def test_safe_shell_blocks_taskkill():
    with pytest.raises(ValueError):
        assert_safe_shell_command("taskkill /PID 123 /F")
