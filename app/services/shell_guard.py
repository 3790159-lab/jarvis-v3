from __future__ import annotations

import shlex

from app.settings import get_settings


class ShellSecurityError(ValueError):
    pass


def validate_shell_command(command: str) -> None:
    settings = get_settings()
    lowered = command.strip().lower()

    for blocked in settings.blocked_shell_patterns:
        if blocked and blocked in lowered:
            raise ShellSecurityError(f"Blocked shell pattern detected: {blocked}")

    try:
        parts = shlex.split(command, posix=False)
    except ValueError as exc:
        raise ShellSecurityError(f"Invalid shell command syntax: {exc}") from exc

    if not parts:
        raise ShellSecurityError("Empty shell command")

    executable = parts[0].lower()
    if executable not in settings.allowed_shell_commands:
        allowed = ", ".join(settings.allowed_shell_commands)
        raise ShellSecurityError(
            f"Command '{executable}' is not allowed. Allowed commands: {allowed}"
        )
