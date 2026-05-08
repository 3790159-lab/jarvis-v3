from __future__ import annotations

import json
import shutil
import subprocess
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ToolResult:
    ok: bool
    tool_type: str
    action: str
    status: str
    message: str
    started_at: str
    finished_at: str
    duration_ms: int
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    policy_summary: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "tool_type": self.tool_type,
            "action": self.action,
            "status": self.status,
            "message": self.message,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "data": self.data,
            "error": self.error,
            "policy_summary": self.policy_summary,
        }


class ToolGateway:
    MAX_RETURN_TEXT = 4000
    MAX_FILE_SIZE_BYTES = 1024 * 256
    MAX_WRITE_SIZE_BYTES = 1024 * 128

    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()
        self.runtime_dir = self.project_root / "artifacts" / "tool_gateway"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

        self.policy = {
            "timeouts": {
                "shell_seconds": 10,
                "http_seconds": 8,
                "filesystem_seconds": 5,
            },
            "allowlist": {
                "shell_commands": {
                    "python": True,
                    "py": True,
                    "cmd": False,
                    "powershell": False,
                    "git": False,
                    "dir": False,
                    "type": False,
                },
                "http_domains": {
                    "127.0.0.1": True,
                    "localhost": True,
                },
                "filesystem_roots": [
                    str((self.project_root / "artifacts").resolve()),
                    str((self.project_root / "state").resolve()),
                ],
            },
        }

    def _policy_summary(self) -> Dict[str, Any]:
        return {
            "timeouts": self.policy["timeouts"],
            "http_domains": sorted(self.policy["allowlist"]["http_domains"].keys()),
            "allowed_shell_commands": sorted(
                [k for k, v in self.policy["allowlist"]["shell_commands"].items() if v]
            ),
            "filesystem_roots": self.policy["allowlist"]["filesystem_roots"],
        }

    def _write_audit(self, payload: Dict[str, Any]) -> None:
        ts = datetime.now().strftime("%Y%m%d")
        path = self.runtime_dir / f"audit_{ts}.log"
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def health(self) -> Dict[str, Any]:
        with self._lock:
            audit_files = list(self.runtime_dir.glob("audit_*.log"))
            return {
                "status": "healthy",
                "runtime_dir": str(self.runtime_dir),
                "project_root": str(self.project_root),
                "shell_timeout_seconds": self.policy["timeouts"]["shell_seconds"],
                "http_timeout_seconds": self.policy["timeouts"]["http_seconds"],
                "filesystem_timeout_seconds": self.policy["timeouts"]["filesystem_seconds"],
                "max_return_text": self.MAX_RETURN_TEXT,
                "max_file_size_bytes": self.MAX_FILE_SIZE_BYTES,
                "max_write_size_bytes": self.MAX_WRITE_SIZE_BYTES,
                "audit_files": len(audit_files),
            }

    def _base_result(
        self,
        ok: bool,
        tool_type: str,
        action: str,
        status: str,
        message: str,
        started_at: str,
        finished_at: str,
        duration_ms: int,
        data: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        result = ToolResult(
            ok=ok,
            tool_type=tool_type,
            action=action,
            status=status,
            message=message,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            data=data,
            error=error,
            policy_summary=self._policy_summary(),
        ).to_dict()
        self._write_audit(result)
        return result

    def _normalize_path(self, raw_path: str) -> Path:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = (self.project_root / candidate).resolve()
        else:
            candidate = candidate.resolve()
        return candidate

    def _is_allowed_path(self, path_obj: Path) -> bool:
        for allowed_root in self.policy["allowlist"]["filesystem_roots"]:
            try:
                path_obj.relative_to(Path(allowed_root).resolve())
                return True
            except Exception:
                continue
        return False

    def _is_allowed_domain(self, url: str) -> bool:
        try:
            parsed = urllib.parse.urlparse(url)
            host = (parsed.hostname or "").lower()
            return bool(self.policy["allowlist"]["http_domains"].get(host))
        except Exception:
            return False

    def _tokenize_command(self, command: str) -> list[str]:
        return [part for part in (command or "").strip().split() if part]

    def _is_allowed_shell_command(self, parts: list[str]) -> bool:
        if not parts:
            return False
        first = parts[0].lower()
        return bool(self.policy["allowlist"]["shell_commands"].get(first))

    def execute_shell(self, command: str) -> Dict[str, Any]:
        started = utc_now()
        start_perf = datetime.now()
        parts = self._tokenize_command(command)

        if not self._is_allowed_shell_command(parts):
            finished = utc_now()
            duration_ms = int((datetime.now() - start_perf).total_seconds() * 1000)
            return self._base_result(
                ok=False,
                tool_type="shell",
                action="execute",
                status="blocked",
                message="Shell command is not in allowlist",
                started_at=started,
                finished_at=finished,
                duration_ms=duration_ms,
                error="blocked_by_policy",
                data={"command": command},
            )

        try:
            proc = subprocess.run(
                parts,
                capture_output=True,
                text=True,
                timeout=self.policy["timeouts"]["shell_seconds"],
                shell=False,
                cwd=str(self.project_root),
            )
            finished = utc_now()
            duration_ms = int((datetime.now() - start_perf).total_seconds() * 1000)

            return self._base_result(
                ok=(proc.returncode == 0),
                tool_type="shell",
                action="execute",
                status="completed" if proc.returncode == 0 else "failed",
                message="Shell command executed",
                started_at=started,
                finished_at=finished,
                duration_ms=duration_ms,
                data={
                    "command": command,
                    "argv": parts,
                    "returncode": proc.returncode,
                    "stdout": (proc.stdout or "")[:self.MAX_RETURN_TEXT],
                    "stderr": (proc.stderr or "")[:self.MAX_RETURN_TEXT],
                },
                error=None if proc.returncode == 0 else "non_zero_returncode",
            )
        except subprocess.TimeoutExpired as exc:
            finished = utc_now()
            duration_ms = int((datetime.now() - start_perf).total_seconds() * 1000)
            return self._base_result(
                ok=False,
                tool_type="shell",
                action="execute",
                status="timeout",
                message="Shell command timed out",
                started_at=started,
                finished_at=finished,
                duration_ms=duration_ms,
                data={"command": command, "argv": parts},
                error=str(exc),
            )
        except Exception as exc:
            finished = utc_now()
            duration_ms = int((datetime.now() - start_perf).total_seconds() * 1000)
            return self._base_result(
                ok=False,
                tool_type="shell",
                action="execute",
                status="failed",
                message="Shell command failed",
                started_at=started,
                finished_at=finished,
                duration_ms=duration_ms,
                data={"command": command, "argv": parts},
                error=str(exc),
            )

    def http_get(self, url: str) -> Dict[str, Any]:
        started = utc_now()
        start_perf = datetime.now()

        if not self._is_allowed_domain(url):
            finished = utc_now()
            duration_ms = int((datetime.now() - start_perf).total_seconds() * 1000)
            return self._base_result(
                ok=False,
                tool_type="http",
                action="get",
                status="blocked",
                message="HTTP domain is not in allowlist",
                started_at=started,
                finished_at=finished,
                duration_ms=duration_ms,
                error="blocked_by_policy",
                data={"url": url},
            )

        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=self.policy["timeouts"]["http_seconds"]) as resp:
                body = resp.read().decode("utf-8", errors="replace")

            finished = utc_now()
            duration_ms = int((datetime.now() - start_perf).total_seconds() * 1000)
            return self._base_result(
                ok=True,
                tool_type="http",
                action="get",
                status="completed",
                message="HTTP GET executed",
                started_at=started,
                finished_at=finished,
                duration_ms=duration_ms,
                data={
                    "url": url,
                    "body": body[:self.MAX_RETURN_TEXT],
                },
            )
        except Exception as exc:
            finished = utc_now()
            duration_ms = int((datetime.now() - start_perf).total_seconds() * 1000)
            return self._base_result(
                ok=False,
                tool_type="http",
                action="get",
                status="failed",
                message="HTTP GET failed",
                started_at=started,
                finished_at=finished,
                duration_ms=duration_ms,
                data={"url": url},
                error=str(exc),
            )

    def file_read(self, path: str) -> Dict[str, Any]:
        started = utc_now()
        start_perf = datetime.now()

        try:
            target = self._normalize_path(path)
        except Exception as exc:
            finished = utc_now()
            duration_ms = int((datetime.now() - start_perf).total_seconds() * 1000)
            return self._base_result(
                ok=False,
                tool_type="filesystem",
                action="read",
                status="failed",
                message="Path normalization failed",
                started_at=started,
                finished_at=finished,
                duration_ms=duration_ms,
                data={"path": path},
                error=str(exc),
            )

        if not self._is_allowed_path(target):
            finished = utc_now()
            duration_ms = int((datetime.now() - start_perf).total_seconds() * 1000)
            return self._base_result(
                ok=False,
                tool_type="filesystem",
                action="read",
                status="blocked",
                message="Filesystem path is outside allowed roots",
                started_at=started,
                finished_at=finished,
                duration_ms=duration_ms,
                error="blocked_by_policy",
                data={"path": str(target)},
            )

        try:
            if not target.exists():
                raise FileNotFoundError(f"File not found: {target}")

            size_bytes = target.stat().st_size
            if size_bytes > self.MAX_FILE_SIZE_BYTES:
                raise ValueError(f"File too large: {size_bytes} bytes")

            text = target.read_text(encoding="utf-8", errors="replace")
            finished = utc_now()
            duration_ms = int((datetime.now() - start_perf).total_seconds() * 1000)

            return self._base_result(
                ok=True,
                tool_type="filesystem",
                action="read",
                status="completed",
                message="File read successfully",
                started_at=started,
                finished_at=finished,
                duration_ms=duration_ms,
                data={
                    "path": str(target),
                    "content": text[:self.MAX_RETURN_TEXT],
                    "size_bytes": size_bytes,
                },
            )
        except Exception as exc:
            finished = utc_now()
            duration_ms = int((datetime.now() - start_perf).total_seconds() * 1000)
            return self._base_result(
                ok=False,
                tool_type="filesystem",
                action="read",
                status="failed",
                message="File read failed",
                started_at=started,
                finished_at=finished,
                duration_ms=duration_ms,
                data={"path": str(target)},
                error=str(exc),
            )

    def file_write(self, path: str, content: str) -> Dict[str, Any]:
        started = utc_now()
        start_perf = datetime.now()

        try:
            target = self._normalize_path(path)
        except Exception as exc:
            finished = utc_now()
            duration_ms = int((datetime.now() - start_perf).total_seconds() * 1000)
            return self._base_result(
                ok=False,
                tool_type="filesystem",
                action="write",
                status="failed",
                message="Path normalization failed",
                started_at=started,
                finished_at=finished,
                duration_ms=duration_ms,
                data={"path": path},
                error=str(exc),
            )

        if not self._is_allowed_path(target):
            finished = utc_now()
            duration_ms = int((datetime.now() - start_perf).total_seconds() * 1000)
            return self._base_result(
                ok=False,
                tool_type="filesystem",
                action="write",
                status="blocked",
                message="Filesystem path is outside allowed roots",
                started_at=started,
                finished_at=finished,
                duration_ms=duration_ms,
                error="blocked_by_policy",
                data={"path": str(target)},
            )

        try:
            encoded = content.encode("utf-8")
            if len(encoded) > self.MAX_WRITE_SIZE_BYTES:
                raise ValueError(f"Write content too large: {len(encoded)} bytes")

            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

            finished = utc_now()
            duration_ms = int((datetime.now() - start_perf).total_seconds() * 1000)
            return self._base_result(
                ok=True,
                tool_type="filesystem",
                action="write",
                status="completed",
                message="File written successfully",
                started_at=started,
                finished_at=finished,
                duration_ms=duration_ms,
                data={
                    "path": str(target),
                    "size_bytes": target.stat().st_size,
                },
            )
        except Exception as exc:
            finished = utc_now()
            duration_ms = int((datetime.now() - start_perf).total_seconds() * 1000)
            return self._base_result(
                ok=False,
                tool_type="filesystem",
                action="write",
                status="failed",
                message="File write failed",
                started_at=started,
                finished_at=finished,
                duration_ms=duration_ms,
                data={"path": str(target)},
                error=str(exc),
            )

    def config_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "project_root": str(self.project_root),
                "policy_summary": self._policy_summary(),
                "which_python": shutil.which("python"),
            }


gateway = ToolGateway(Path(__file__).resolve().parents[2])
