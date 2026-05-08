from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import requests

from app.infra.logger import logger
from app.settings import Settings


class ToolExecutor:
    SAFE_COMMAND_PREFIXES = (
        "Get-ChildItem",
        "Get-Content",
        "Set-Location",
        "pwd",
        "dir",
        "echo",
        "python --version",
        "python -V",
        "git status",
    )

    def __init__(self) -> None:
        self.project_root = Path(Settings.DEFAULT_PROJECT_ROOT)

    def _is_safe_command(self, command: str) -> bool:
        command = command.strip()
        if Settings.ENABLE_DANGEROUS_TOOLS:
            return True
        return any(command.startswith(prefix) for prefix in self.SAFE_COMMAND_PREFIXES)

    def run_shell_command(self, command: str, timeout: int = 30) -> dict[str, Any]:
        logger.info(f"run_shell_command: {command}")

        if not self._is_safe_command(command):
            return {
                "success": False,
                "error": "Command is blocked by safety policy",
                "stdout": "",
                "stderr": "",
                "returncode": None,
            }

        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
            )
            return {
                "success": completed.returncode == 0,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "returncode": completed.returncode,
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": f"Command timed out after {timeout}s",
                "stdout": "",
                "stderr": "",
                "returncode": None,
            }
        except Exception as e:
            logger.exception("Shell command failed")
            return {
                "success": False,
                "error": str(e),
                "stdout": "",
                "stderr": "",
                "returncode": None,
            }

    def read_file(self, relative_path: str) -> dict[str, Any]:
        try:
            file_path = self.project_root / relative_path
            content = file_path.read_text(encoding="utf-8")
            return {"success": True, "content": content}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def write_file(self, relative_path: str, content: str) -> dict[str, Any]:
        try:
            file_path = self.project_root / relative_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return {"success": True, "path": str(file_path)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_directory(self, relative_path: str = ".") -> dict[str, Any]:
        try:
            dir_path = self.project_root / relative_path
            items = []
            for item in dir_path.iterdir():
                items.append({
                    "name": item.name,
                    "is_dir": item.is_dir(),
                })
            return {"success": True, "items": items}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def http_request(
        self,
        method: str,
        url: str,
        json_body: dict[str, Any] | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        try:
            response = requests.request(
                method=method.upper(),
                url=url,
                json=json_body,
                timeout=timeout,
            )
            return {
                "success": response.ok,
                "status_code": response.status_code,
                "text": response.text[:5000],
            }
        except Exception as e:
            return {"success": False, "error": str(e)}


tool_executor = ToolExecutor()