"""Read-only observation collectors for the Jarvis admin console (Ступень 1).

Pure, side-effect-free helpers used by the admin-only `/git_status`, `/regress`,
`/logs_tail`, `/health` commands. **No mutations** — this module never writes
files, never touches git state, never signals processes. Git access is limited
to read-only verbs (see ``_GIT_READONLY_VERBS``).
"""
from __future__ import annotations

from pathlib import Path

# --- path constants (relative to the bot repo root C:\jarvis) ---------------
ROOT = Path.cwd()
LOG_PATH = ROOT / "logs" / "jarvis_bot.log"
PID_PATH = ROOT / "state" / "bot.pid"
HEARTBEAT_PATH = ROOT / "state" / "bot_heartbeat.txt"
CLOUDFLARED_LOG = ROOT / "logs" / "cloudflared.log"
BASELINE_PATH = ROOT / "state" / "regress_baseline.json"

# --- read-only git guard -----------------------------------------------------
_GIT_READONLY_VERBS = {"status", "rev-parse", "log", "describe"}

# --- log noise filter --------------------------------------------------------
_NOISE_LOGGER = "_base_client"
_NOISE_MARKER = "Request options"
_MAX_LINE_LEN = 2000


def filter_log_noise(lines: list[str]) -> list[str]:
    """Drop giant base64 request-dump lines from an SDK `*._base_client` logger.

    A line is noise if it comes from a ``_base_client`` logger AND mentions
    ``Request options``, or if it is simply longer than ``_MAX_LINE_LEN``.
    """
    kept = []
    for line in lines:
        if _NOISE_LOGGER in line and _NOISE_MARKER in line:
            continue
        if len(line) > _MAX_LINE_LEN:
            continue
        kept.append(line)
    return kept


def tail_log(path: str, n: int = 40, max_chars: int = 3900) -> str:
    """Read a log file, filter base64 noise, keep the last ``n`` lines.

    Result is truncated to ``max_chars`` from the bottom (keeps newest text).
    """
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return "(лог не найден)"
    kept = filter_log_noise(raw)
    tail = kept[-n:]
    text = "\n".join(tail)
    if len(text) > max_chars:
        text = text[-max_chars:]
    return text
