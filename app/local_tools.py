from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .message_models import ExecutionResult
from .safety import PathGuard, command_needs_approval


class LocalTools:
    def __init__(self, allowed_roots: list[str] | None = None) -> None:
        self.guard = PathGuard(allowed_roots)

    def fs_list(self, path: str) -> ExecutionResult:
        safe_path = self.guard.ensure_allowed(path)
        entries = []
        for item in sorted(Path(safe_path).iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
            entries.append({
                "name": item.name,
                "type": "dir" if item.is_dir() else "file",
                "path": str(item),
            })
        preview = ", ".join(x["name"] for x in entries[:12]) or "папка пуста"
        return ExecutionResult(
            ok=True,
            tool="fs_list",
            summary=f"Найдено {len(entries)} элементов: {preview}",
            details={"path": safe_path, "entries": entries},
        )

    def fs_read(self, path: str, max_chars: int = 8000) -> ExecutionResult:
        safe_path = self.guard.ensure_allowed(path)
        content = Path(safe_path).read_text(encoding="utf-8", errors="replace")
        clipped = content[:max_chars]
        return ExecutionResult(
            ok=True,
            tool="fs_read",
            summary=f"Прочитан файл {os.path.basename(safe_path)} ({len(content)} chars)",
            details={"path": safe_path, "content": clipped, "truncated": len(content) > max_chars},
        )

    def fs_search(self, path: str, query: str, max_results: int = 20) -> ExecutionResult:
        safe_path = self.guard.ensure_allowed(path)
        results: list[dict[str, str | int]] = []
        for root, _, files in os.walk(safe_path):
            for file_name in files:
                file_path = os.path.join(root, file_name)
                try:
                    text = Path(file_path).read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                for lineno, line in enumerate(text.splitlines(), start=1):
                    if query.lower() in line.lower():
                        results.append(
                            {
                                "path": file_path,
                                "line": lineno,
                                "snippet": line.strip()[:240],
                            }
                        )
                        if len(results) >= max_results:
                            break
                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
                break
        return ExecutionResult(
            ok=True,
            tool="fs_search",
            summary=f"По запросу '{query}' найдено {len(results)} совпадений",
            details={"path": safe_path, "query": query, "results": results},
        )

    def git_status(self, path: str) -> ExecutionResult:
        safe_path = self.guard.ensure_allowed(path)
        completed = subprocess.run(
            ["git", "-C", safe_path, "status", "--short", "--branch"],
            capture_output=True,
            text=True,
            check=False,
        )
        return ExecutionResult(
            ok=completed.returncode == 0,
            tool="git_status",
            summary=(completed.stdout or completed.stderr).strip() or "git status returned empty output",
            details={"path": safe_path, "returncode": completed.returncode},
        )

    def run_python(self, path: str, code: str) -> ExecutionResult:
        safe_path = self.guard.ensure_allowed(path)
        completed = subprocess.run(
            ["python", "-c", code],
            capture_output=True,
            text=True,
            cwd=safe_path,
            check=False,
        )
        output = (completed.stdout + "\n" + completed.stderr).strip()
        return ExecutionResult(
            ok=completed.returncode == 0,
            tool="run_python",
            summary=output[:1000] or "python completed with no output",
            details={"path": safe_path, "returncode": completed.returncode},
        )

    def run_shell(self, path: str, command: str, approval_granted: bool = False) -> ExecutionResult:
        safe_path = self.guard.ensure_allowed(path)
        if command_needs_approval(command) and not approval_granted:
            return ExecutionResult(
                ok=False,
                tool="run_shell",
                summary="Команда требует подтверждения и не была выполнена",
                details={"path": safe_path, "command": command, "approval_required": True},
            )
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            cwd=safe_path,
            check=False,
        )
        output = (completed.stdout + "\n" + completed.stderr).strip()
        return ExecutionResult(
            ok=completed.returncode == 0,
            tool="run_shell",
            summary=output[:1400] or "shell completed with no output",
            details={"path": safe_path, "returncode": completed.returncode, "command": command},
        )
