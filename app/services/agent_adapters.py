from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import time
from typing import Any, Dict, List

import requests

from app.services.text_normalizer import normalize_model_text


def _result(ok: bool, adapter: str, output: Any = None, error: str | None = None, metadata: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return {
        "ok": ok,
        "adapter": adapter,
        "output": output,
        "error": error,
        "metadata": metadata or {},
    }


def _is_placeholder_url(url: str) -> bool:
    value = (url or "").strip().lower()
    if not value:
        return True
    return (
        "your-openai-compatible-endpoint" in value
        or "example.com" in value
        or (value.endswith("/v1") and "http" not in value)
    )


def _normalize_base_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def _resolve_command_parts(command_line: str) -> List[str]:
    raw = (command_line or "").strip()
    if not raw:
        return []

    try:
        parts = shlex.split(raw, posix=False)
    except Exception:
        parts = [raw]

    cleaned = [p.strip().strip('"') for p in parts if str(p).strip()]
    if not cleaned:
        return []

    exe = cleaned[0]
    if os.path.isfile(exe):
        resolved = exe
    else:
        found = shutil.which(exe)
        if not found:
            return []
        resolved = found

    return [resolved, *cleaned[1:]]


def _parse_retry_after(value: str | None, attempt: int) -> float:
    if value:
        try:
            parsed = float(value)
            if parsed > 0:
                return min(parsed, 15.0)
        except Exception:
            pass
    return min(float(2 ** (attempt - 1)), 8.0)


def _post_json_with_retry(
    url: str,
    *,
    headers: Dict[str, str] | None = None,
    json_body: Dict[str, Any] | None = None,
    timeout: int = 60,
    max_attempts: int = 3,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.post(
                url,
                headers=headers,
                json=json_body,
                timeout=timeout,
            )

            if response.status_code == 429 and attempt < max_attempts:
                delay = _parse_retry_after(response.headers.get("Retry-After"), attempt)
                time.sleep(delay)
                continue

            response.raise_for_status()
            return response.json(), {"attempt": attempt}

        except requests.HTTPError as exc:
            last_exc = exc
            resp = getattr(exc, "response", None)
            if resp is not None and resp.status_code == 429 and attempt < max_attempts:
                delay = _parse_retry_after(resp.headers.get("Retry-After"), attempt)
                time.sleep(delay)
                continue
            raise

        except Exception as exc:
            last_exc = exc
            text = str(exc).lower()
            transient = any(token in text for token in ["timed out", "connection", "temporarily", "429"])
            if transient and attempt < max_attempts:
                time.sleep(_parse_retry_after(None, attempt))
                continue
            raise

    if last_exc:
        raise last_exc
    raise RuntimeError("HTTP retry loop exited unexpectedly")


def _openai_enabled() -> bool:
    base_url = _normalize_base_url(os.getenv("OPENAI_COMPAT_BASE_URL", ""))
    api_key = (os.getenv("OPENAI_COMPAT_API_KEY", "") or "").strip()
    if not base_url or not api_key:
        return False
    if _is_placeholder_url(base_url):
        return False
    return True


def _claude_enabled() -> bool:
    command = os.getenv("CLAUDE_CODE_COMMAND", "") or "claude"
    return len(_resolve_command_parts(command)) > 0


def list_adapters() -> List[Dict[str, Any]]:
    return [
        {
            "name": "local_echo",
            "enabled": True,
            "type": "builtin",
            "capabilities": ["chat", "reasoning", "code"],
            "notes": "Safe local test adapter that echoes and wraps the prompt.",
        },
        {
            "name": "ollama_http",
            "enabled": True,
            "type": "http",
            "capabilities": ["chat", "reasoning", "code", "local_ai"],
            "notes": "Uses local Ollama HTTP API.",
        },
        {
            "name": "openai_compatible_http",
            "enabled": _openai_enabled(),
            "type": "http",
            "capabilities": ["chat", "reasoning", "code", "remote_ai", "external_ai"],
            "notes": "Uses OpenAI-compatible chat/completions endpoint with retry/backoff and trimmed credentials.",
        },
        {
            "name": "claude_code_bridge",
            "enabled": _claude_enabled(),
            "type": "cli",
            "capabilities": ["code", "reasoning", "external_ai"],
            "notes": "Uses Claude Code CLI in non-interactive print mode (-p).",
        },
    ]


def invoke_adapter(adapter_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    adapter = (adapter_name or "").strip().lower()
    payload = dict(payload or {})

    if adapter == "local_echo":
        prompt = str(payload.get("prompt") or payload.get("message") or "")
        text = normalize_model_text(f"[local_echo] {prompt}")
        return _result(
            ok=True,
            adapter=adapter,
            output={"text": text},
        )

    if adapter == "ollama_http":
        base_url = _normalize_base_url(str(payload.get("base_url") or os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434"))
        model = str(payload.get("model") or os.getenv("OLLAMA_MODEL") or "llama3.2:latest")
        prompt = str(payload.get("prompt") or payload.get("message") or "")
        timeout_seconds = int(payload.get("timeout_seconds") or 60)

        try:
            data, meta = _post_json_with_retry(
                f"{base_url}/api/generate",
                json_body={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                },
                timeout=timeout_seconds,
                max_attempts=2,
            )
            text = normalize_model_text(data.get("response") or "")
            return _result(
                ok=True,
                adapter=adapter,
                output={"text": text, "raw": data},
                metadata={"model": model, **meta},
            )
        except Exception as exc:
            return _result(ok=False, adapter=adapter, error=str(exc))

    if adapter == "openai_compatible_http":
        base_url = _normalize_base_url(str(payload.get("base_url") or os.getenv("OPENAI_COMPAT_BASE_URL") or ""))
        api_key = str(payload.get("api_key") or os.getenv("OPENAI_COMPAT_API_KEY") or "").strip()
        model = str(payload.get("model") or os.getenv("OPENAI_COMPAT_MODEL") or "gpt-4o-mini")
        prompt = str(payload.get("prompt") or payload.get("message") or "")
        timeout_seconds = int(payload.get("timeout_seconds") or 60)

        if not base_url or not api_key:
            return _result(ok=False, adapter=adapter, error="OPENAI_COMPAT_BASE_URL or OPENAI_COMPAT_API_KEY is not configured")

        if _is_placeholder_url(base_url):
            return _result(ok=False, adapter=adapter, error="OPENAI_COMPAT_BASE_URL still contains a placeholder value. Set a real endpoint.")

        try:
            data, meta = _post_json_with_retry(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json_body={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=timeout_seconds,
                max_attempts=3,
            )

            text = ""
            try:
                text = data["choices"][0]["message"]["content"]
            except Exception:
                text = str(data)

            text = normalize_model_text(text)

            return _result(
                ok=True,
                adapter=adapter,
                output={"text": text, "raw": data},
                metadata={"model": model, "base_url": base_url, **meta},
            )
        except Exception as exc:
            return _result(ok=False, adapter=adapter, error=str(exc), metadata={"base_url": base_url})

    if adapter == "claude_code_bridge":
        command = str(payload.get("command") or os.getenv("CLAUDE_CODE_COMMAND") or "claude")
        prompt = str(payload.get("prompt") or payload.get("message") or "")
        timeout_seconds = int(payload.get("timeout_seconds") or 180)
        working_directory = str(payload.get("working_directory") or ".")

        resolved_parts = _resolve_command_parts(command)
        if not resolved_parts:
            return _result(
                ok=False,
                adapter=adapter,
                error="CLAUDE_CODE_COMMAND is not resolvable. Check the executable path or command name.",
                metadata={"attempted_command": command},
            )

        cmd = [*resolved_parts, "-p", prompt]

        try:
            started = time.time()
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                cwd=working_directory,
                shell=False,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
                env={**os.environ, "NO_COLOR": "1"},
            )
            duration_ms = int((time.time() - started) * 1000)

            stdout = normalize_model_text(proc.stdout or "")
            stderr = normalize_model_text(proc.stderr or "")

            return _result(
                ok=(proc.returncode == 0),
                adapter=adapter,
                output={
                    "stdout": stdout,
                    "stderr": stderr,
                    "text": stdout.strip(),
                    "returncode": proc.returncode,
                },
                error=None if proc.returncode == 0 else "Claude bridge command failed",
                metadata={
                    "duration_ms": duration_ms,
                    "resolved_parts": cmd,
                },
            )
        except Exception as exc:
            return _result(
                ok=False,
                adapter=adapter,
                error=str(exc),
                metadata={"resolved_parts": cmd},
            )

    return _result(ok=False, adapter=adapter_name, error=f"Unknown adapter: {adapter_name}")