import os
import subprocess
from typing import Any, Dict

from app.services.policy import get_policy


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ARTIFACTS_DIR = os.path.join(BASE_DIR, "artifacts")
OUTPUT_DIR = os.path.join(ARTIFACTS_DIR, "output")

os.makedirs(OUTPUT_DIR, exist_ok=True)


def _sanitize_filename(name: str) -> str:
    bad = '<>:"/\\|?*'
    cleaned = "".join("_" if ch in bad else ch for ch in (name or "artifact.txt"))
    cleaned = cleaned.strip().replace(" ", "_")
    return (cleaned or "artifact.txt")[:120]


def safe_write_text_file(filename: str, content: str) -> Dict[str, Any]:
    policy = get_policy()
    if not policy.get("allow_file_write", True):
        raise RuntimeError("File writing disabled by policy")

    max_chars = int(policy.get("max_text_artifact_chars", 12000))
    filename = _sanitize_filename(filename)
    path = os.path.join(OUTPUT_DIR, filename)
    data = str(content or "")
    if len(data) > max_chars:
        data = data[:max_chars] + "\n\n[truncated by policy]"
    with open(path, "w", encoding="utf-8") as f:
        f.write(data)
    return {"path": path, "chars_written": len(data)}


def safe_run_shell(command: str) -> Dict[str, Any]:
    policy = get_policy()
    if not policy.get("shell_enabled", False):
        raise RuntimeError("Shell execution disabled by policy")

    cmd = str(command or "").strip()
    if not cmd:
        raise RuntimeError("Empty shell command")

    prefixes = [p.lower() for p in policy.get("allow_shell_prefixes", [])]
    cmd_lower = cmd.lower()
    if not any(cmd_lower.startswith(prefix) for prefix in prefixes):
        raise RuntimeError(f"Command blocked by policy: {cmd}")

    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", cmd],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )

    output = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")
    if len(output) > 8000:
        output = output[:8000] + "\n\n[truncated]"
    return {
        "returncode": int(completed.returncode),
        "output": output.strip(),
        "command": cmd,
    }