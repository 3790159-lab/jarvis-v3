"""Read-only observation collectors for the Jarvis admin console (Ступень 1).

Pure, side-effect-free helpers used by the admin-only `/git_status`, `/regress`,
`/logs_tail`, `/health` commands. **No mutations** — this module never writes
files, never touches git state, never signals processes. Git access is limited
to read-only verbs (see ``_GIT_READONLY_VERBS``).
"""
from __future__ import annotations

import re
import subprocess
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

# --- health thresholds -------------------------------------------------------
HEARTBEAT_STALE_S = 90
TUNNEL_STALE_S = 300

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


def git_status_text(repo: str = "C:/jarvis", run=subprocess.run) -> str:
    """Read-only git snapshot: branch, short HEAD, ahead/behind, dirty count.

    Uses only verbs in ``_GIT_READONLY_VERBS`` via ``git -C <repo> <verb>``.
    ``run`` is injectable for tests. Never mutates git state.
    """
    def _git(*verb_and_args):
        verb = verb_and_args[0]
        assert verb in _GIT_READONLY_VERBS, f"non-readonly git verb: {verb}"
        res = run(["git", "-C", repo, *verb_and_args], capture_output=True, text=True)
        return (getattr(res, "stdout", "") or "").strip()

    short_head = _git("rev-parse", "--short", "HEAD") or "?"
    status = _git("status", "-sb")

    lines = status.splitlines()
    branch_line = lines[0] if lines else ""
    # "## branch...upstream [ahead N, behind M]"
    branch = branch_line.lstrip("# ").split("...")[0].split(" ")[0] if branch_line else "?"
    ahead_behind = ""
    if "[" in branch_line and "]" in branch_line:
        ahead_behind = branch_line[branch_line.index("[") + 1:branch_line.index("]")]
    dirty = [ln for ln in lines[1:] if ln.strip()]
    dirty_txt = f"{len(dirty)} изменённых" if dirty else "чисто"

    out = [
        f"🌿 ветка: {branch}",
        f"📍 HEAD: {short_head}",
    ]
    if ahead_behind:
        out.append(f"↕️ {ahead_behind}")
    out.append(f"🧹 рабочее дерево: {dirty_txt}")
    return "\n".join(out)


def health_snapshot(readers: dict) -> str:
    """Assemble the /health report from injected reader callables.

    ``readers`` keys: ``bot`` -> {pid, alive}, ``heartbeat_age`` -> seconds,
    ``backend`` -> {ok, code}, ``tunnel_age`` -> seconds, ``disk`` ->
    {free_gb, total_gb}. Every reader is called defensively; a failing reader
    degrades its own line to a ❓ rather than crashing the report.
    """
    def _safe(key, fn):
        try:
            return readers[key]()
        except Exception as e:  # a broken probe must not kill the whole report
            return e

    lines = []

    bot = _safe("bot", None)
    if isinstance(bot, dict):
        mark = "✅" if bot.get("alive") else "🔴"
        started = bot.get("started")
        suffix = f" (старт {started})" if started else ""
        lines.append(f"🤖 бот: PID {bot.get('pid', '?')}{suffix} {mark}")
    else:
        lines.append("🤖 бот: ❓")

    hb = _safe("heartbeat_age", None)
    if isinstance(hb, (int, float)):
        mark = "✅" if hb <= HEARTBEAT_STALE_S else "⚠️"
        lines.append(f"💓 heartbeat: {int(hb)}с назад {mark}")
    else:
        lines.append("💓 heartbeat: ❓")

    be = _safe("backend", None)
    if isinstance(be, dict):
        mark = "✅" if be.get("ok") else "🔴"
        lines.append(f"🔌 backend: {mark} (код {be.get('code', '—')})")
    else:
        lines.append("🔌 backend: 🔴 недоступен")

    tun = _safe("tunnel_age", None)
    if isinstance(tun, (int, float)):
        mark = "✅" if tun < TUNNEL_STALE_S else "⚠️"
        lines.append(f"🌐 туннель: {int(tun)}с назад {mark}")
    else:
        lines.append("🌐 туннель: ❓")

    disk = _safe("disk", None)
    if isinstance(disk, dict):
        lines.append(f"💽 диск: {disk.get('free_gb', '?')}/{disk.get('total_gb', '?')} ГБ свободно")
    else:
        lines.append("💽 диск: ❓")

    return "\n".join(lines)


def parse_pytest_summary(stdout: str) -> dict:
    """Pull ``failed``/``passed``/``errors`` counts from pytest's summary tail.

    Reads the last matching occurrence of each ``N <word>`` token so a stray
    earlier mention doesn't win. Missing counts default to 0.
    """
    def _last(word):
        matches = re.findall(rf"(\d+)\s+{word}", stdout)
        return int(matches[-1]) if matches else 0

    return {
        "failed": _last("failed"),
        "passed": _last("passed"),
        "errors": _last("error"),  # matches "error" and "errors"
    }


def regress_verdict(summary: dict, baseline: dict | None) -> str:
    """Compare a fresh pytest summary against the stored baseline failed-count."""
    failed = summary.get("failed", 0)
    passed = summary.get("passed", 0)
    errors = summary.get("errors", 0)
    body = f"{failed} failed, {passed} passed, {errors} errors"
    if baseline is None:
        return f"⚠️ baseline не задан (обнови вручную)\n{body}"
    base_failed = baseline.get("failed", 0)
    if failed <= base_failed:
        return f"✅ не хуже baseline ({failed} ≤ {base_failed})\n{body}"
    return f"⚠️ регресс: +{failed - base_failed} новых падений (было {base_failed})\n{body}"


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
