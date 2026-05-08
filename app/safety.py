from __future__ import annotations

import os
from pathlib import Path


DEFAULT_ALLOWED_ROOTS = [
    r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram",
    r"C:\Users\Daniil Lapin\Documents",
]

DESTRUCTIVE_TOKENS = {
    "del",
    "erase",
    "rm",
    "rmdir",
    "format",
    "shutdown",
    "restart",
    "reboot",
    "taskkill",
    "sc delete",
    "git reset --hard",
    "git clean -fd",
    "move-item",
    "remove-item",
    "set-content",
    "add-content",
}

READ_ONLY_TOOLS = {"fs_list", "fs_read", "fs_search", "git_status"}


def normalize_path(path: str) -> str:
    return str(Path(path).expanduser().resolve())


class PathGuard:
    def __init__(self, allowed_roots: list[str] | None = None) -> None:
        roots = allowed_roots or DEFAULT_ALLOWED_ROOTS
        self.allowed_roots = [normalize_path(p) for p in roots]

    def ensure_allowed(self, path: str) -> str:
        target = normalize_path(path)
        for root in self.allowed_roots:
            if os.path.commonpath([root, target]) == root:
                return target
        raise PermissionError(f"Path is outside allowed roots: {target}")



def command_needs_approval(command: str) -> bool:
    lowered = command.lower()
    return any(token in lowered for token in DESTRUCTIVE_TOKENS)
