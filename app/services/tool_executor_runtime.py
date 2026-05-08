from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from app.services.tool_registry import get_tool_names


PROJECT_ROOT = Path.cwd()
RUNTIME_BASE = PROJECT_ROOT / "jarvis_stage3_artifacts" / "tool_runtime"
LOG_DIR = RUNTIME_BASE / "logs"
OUTPUT_DIR = RUNTIME_BASE / "outputs"

LOG_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


BLOCKED_SHELL_PATTERNS = [
    "format ",
    "shutdown",
    "restart-computer",
    "stop-computer",
    "remove-item c:\\",
    "remove-item /s",
    "del /s",
    "rd /s",
    "reg delete",
    "bcdedit",
    "diskpart",
    "cipher /w",
]


def _utc_ms() -> int:
    return int(time.time() * 1000)


def _log(tool: str, message: str) -> None:
    path = LOG_DIR / f"{tool}.log"
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def _result(
    *,
    ok: bool,
    tool: str,
    output: Any = None,
    error: Optional[str] = None,
    exit_code: Optional[int] = None,
    duration_ms: int = 0,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "ok": ok,
        "tool": tool,
        "output": output,
        "error": error,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "metadata": metadata or {},
    }


def _safe_path(path_str: str) -> Path:
    raw = Path(path_str)
    if not raw.is_absolute():
        raw = PROJECT_ROOT / raw

    resolved = raw.resolve()
    allowed_roots = [
        PROJECT_ROOT.resolve(),
        OUTPUT_DIR.resolve(),
        (PROJECT_ROOT / "jarvis_stage3_artifacts").resolve(),
    ]

    if not any(str(resolved).startswith(str(root)) for root in allowed_roots):
        raise ValueError(f"Path is outside allowed roots: {resolved}")

    return resolved


def _check_shell_command(command: str) -> None:
    normalized = (command or "").strip().lower()
    if not normalized:
        raise ValueError("Shell command is empty")

    for pattern in BLOCKED_SHELL_PATTERNS:
        if pattern in normalized:
            raise ValueError(f"Blocked shell pattern detected: {pattern}")


def execute_shell(payload: Dict[str, Any]) -> Dict[str, Any]:
    started = _utc_ms()
    command = str(payload.get("command") or "").strip()
    timeout_seconds = int(payload.get("timeout_seconds") or 30)
    working_directory = payload.get("working_directory") or str(PROJECT_ROOT)

    try:
        _check_shell_command(command)
        wd = _safe_path(str(working_directory))
        _log("shell", f"START command={command!r} wd={wd}")

        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            cwd=str(wd),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )

        output = {
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
        _log("shell", f"END exit_code={proc.returncode}")

        return _result(
            ok=proc.returncode == 0,
            tool="shell",
            output=output,
            error=None if proc.returncode == 0 else "Shell command failed",
            exit_code=proc.returncode,
            duration_ms=_utc_ms() - started,
            metadata={"working_directory": str(wd)},
        )
    except Exception as exc:
        _log("shell", f"ERROR {exc}")
        return _result(
            ok=False,
            tool="shell",
            output=None,
            error=str(exc),
            exit_code=-1,
            duration_ms=_utc_ms() - started,
        )


def execute_python(payload: Dict[str, Any]) -> Dict[str, Any]:
    started = _utc_ms()
    code = str(payload.get("code") or "")
    timeout_seconds = int(payload.get("timeout_seconds") or 30)

    try:
        python_exe = Path(".venv/Scripts/python.exe")
        if python_exe.exists():
            py = str(python_exe.resolve())
        else:
            py = "python"

        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as fh:
            fh.write(code)
            temp_path = fh.name

        _log("python", f"START temp_path={temp_path}")
        proc = subprocess.run(
            [py, temp_path],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )

        try:
            os.remove(temp_path)
        except Exception:
            pass

        output = {
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
        _log("python", f"END exit_code={proc.returncode}")

        return _result(
            ok=proc.returncode == 0,
            tool="python",
            output=output,
            error=None if proc.returncode == 0 else "Python execution failed",
            exit_code=proc.returncode,
            duration_ms=_utc_ms() - started,
        )
    except Exception as exc:
        _log("python", f"ERROR {exc}")
        return _result(
            ok=False,
            tool="python",
            output=None,
            error=str(exc),
            exit_code=-1,
            duration_ms=_utc_ms() - started,
        )


def execute_http(payload: Dict[str, Any]) -> Dict[str, Any]:
    started = _utc_ms()
    method = str(payload.get("method") or "GET").upper()
    url = str(payload.get("url") or "").strip()
    headers = payload.get("headers") or {}
    json_payload = payload.get("json")
    timeout_seconds = int(payload.get("timeout_seconds") or 20)

    try:
        if not url.startswith(("http://", "https://")):
            raise ValueError("HTTP URL must start with http:// or https://")

        _log("http", f"START method={method} url={url}")
        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            json=json_payload,
            timeout=timeout_seconds,
        )

        content_type = response.headers.get("content-type", "")
        body: Any
        if "application/json" in content_type:
            try:
                body = response.json()
            except Exception:
                body = response.text
        else:
            body = response.text

        _log("http", f"END status={response.status_code}")
        return _result(
            ok=200 <= response.status_code < 300,
            tool="http",
            output={
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body": body,
            },
            error=None if 200 <= response.status_code < 300 else "HTTP request failed",
            exit_code=response.status_code,
            duration_ms=_utc_ms() - started,
            metadata={"url": url, "method": method},
        )
    except Exception as exc:
        _log("http", f"ERROR {exc}")
        return _result(
            ok=False,
            tool="http",
            output=None,
            error=str(exc),
            exit_code=-1,
            duration_ms=_utc_ms() - started,
            metadata={"url": url, "method": method},
        )


def execute_file_write(payload: Dict[str, Any]) -> Dict[str, Any]:
    started = _utc_ms()
    try:
        path = _safe_path(str(payload.get("path") or ""))
        content = str(payload.get("content") or "")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        _log("file_write", f"WROTE path={path}")
        return _result(
            ok=True,
            tool="file_write",
            output={"path": str(path), "bytes_written": len(content.encode("utf-8"))},
            duration_ms=_utc_ms() - started,
        )
    except Exception as exc:
        _log("file_write", f"ERROR {exc}")
        return _result(
            ok=False,
            tool="file_write",
            output=None,
            error=str(exc),
            exit_code=-1,
            duration_ms=_utc_ms() - started,
        )


def execute_file_read(payload: Dict[str, Any]) -> Dict[str, Any]:
    started = _utc_ms()
    try:
        path = _safe_path(str(payload.get("path") or ""))
        content = path.read_text(encoding="utf-8")
        _log("file_read", f"READ path={path}")
        return _result(
            ok=True,
            tool="file_read",
            output={"path": str(path), "content": content},
            duration_ms=_utc_ms() - started,
        )
    except Exception as exc:
        _log("file_read", f"ERROR {exc}")
        return _result(
            ok=False,
            tool="file_read",
            output=None,
            error=str(exc),
            exit_code=-1,
            duration_ms=_utc_ms() - started,
        )


def execute_tool(tool_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if tool_name not in get_tool_names():
        return _result(
            ok=False,
            tool=tool_name,
            output=None,
            error=f"Unknown or disabled tool: {tool_name}",
            exit_code=-1,
            duration_ms=0,
        )

    if tool_name == "shell":
        return execute_shell(payload)
    if tool_name == "python":
        return execute_python(payload)
    if tool_name == "http":
        return execute_http(payload)
    if tool_name == "file_write":
        return execute_file_write(payload)
    if tool_name == "file_read":
        return execute_file_read(payload)

    return _result(
        ok=False,
        tool=tool_name,
        output=None,
        error=f"Tool dispatcher missing implementation for: {tool_name}",
        exit_code=-1,
        duration_ms=0,
    )