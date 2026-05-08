from __future__ import annotations

import re


SAFE_SHELL_PREFIXES = [
    "dir",
    "ls",
    "pwd",
    "echo",
    "type",
    "Get-ChildItem",
    "Get-Location",
    "Get-Content",
    "python -m pytest",
]

DENY_PATTERNS = [
    r"\brm\b",
    r"\bdel\b",
    r"\brmdir\b",
    r"\bformat\b",
    r"\bshutdown\b",
    r"\brestart-computer\b",
    r"\btaskkill\b",
    r"\bStop-Process\b",
    r">\s*[A-Za-z]:\\",
]


def assert_safe_shell_command(command: str) -> None:
    normalized = command.strip()

    for pattern in DENY_PATTERNS:
        if re.search(pattern, normalized, flags=re.IGNORECASE):
            raise ValueError(f"Unsafe shell command blocked by deny pattern: {pattern}")

    allowed = any(
        normalized.lower().startswith(prefix.lower())
        for prefix in SAFE_SHELL_PREFIXES
    )

    if not allowed:
        raise ValueError("Shell command is not in safe allowlist")
