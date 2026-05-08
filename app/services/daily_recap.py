"""Daily Recap Generator — Block H5.2.

Analyzes the day's decisions, photos, errors, and feedback.
Saves recap to Obsidian and formats for Telegram morning brief.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).parent.parent.parent
_DECISIONS_PATH = _ROOT / "state" / "decisions.jsonl"
_IMAGE_LIB_PATH = _ROOT / "state" / "image_library.jsonl"
_ERRORS_LOG = _ROOT / "state" / "logs" / "errors.jsonl"
_RECAPS_DIR = _ROOT / "state" / "daily_recaps"
_RECAPS_DIR.mkdir(parents=True, exist_ok=True)


def _read_jsonl(path: Path, since: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Read a JSONL file, optionally filtering to entries since *since*."""
    if not path.exists():
        return []
    records = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if since:
                    ts_str = rec.get("timestamp") or rec.get("ts") or rec.get("created_at") or ""
                    try:
                        ts = datetime.fromisoformat(ts_str[:19])
                        if ts < since:
                            continue
                    except (ValueError, TypeError):
                        pass
                records.append(rec)
            except json.JSONDecodeError:
                continue
    except Exception as exc:
        logger.warning("[daily_recap] read error %s: %s", path, exc)
    return records


def generate_daily_recap(date_str: Optional[str] = None) -> Dict[str, Any]:
    """Analyze today's activity and return structured recap dict."""
    today = datetime.now().date() if date_str is None else date.fromisoformat(date_str)
    date_label = today.isoformat()
    since = datetime.combine(today, datetime.min.time())

    decisions = _read_jsonl(_DECISIONS_PATH, since=since)
    images = _read_jsonl(_IMAGE_LIB_PATH, since=since)
    errors = _read_jsonl(_ERRORS_LOG, since=since)

    tasks_completed = sum(1 for d in decisions if d.get("outcome") in ("success", "done"))
    photos_generated = len(images)
    errors_count = len(errors)

    positive_feedback = sum(1 for d in decisions if d.get("feedback") == "positive")
    negative_feedback = sum(1 for d in decisions if d.get("feedback") == "negative")

    # Top intents
    intent_counts: Dict[str, int] = {}
    for d in decisions:
        intent = d.get("intent") or d.get("selected_intent") or "unknown"
        intent_counts[intent] = intent_counts.get(intent, 0) + 1
    top_intents = sorted(intent_counts, key=lambda k: -intent_counts[k])[:5]

    # Learnings from negative feedback
    learnings: List[str] = []
    for d in decisions:
        if d.get("feedback") == "negative" and d.get("query"):
            q = str(d["query"])[:80]
            learnings.append(f"Улучшить: «{q}»")
    learnings = learnings[:3]

    # Tomorrow focus
    tomorrow_focus = "Продолжить текущие задачи"
    if negative_feedback > positive_feedback:
        tomorrow_focus = "Анализ и улучшение качества ответов"
    elif photos_generated > 5:
        tomorrow_focus = "Оптимизация контент-генерации"

    recap = {
        "date": date_label,
        "tasks_completed": tasks_completed,
        "photos_generated": photos_generated,
        "errors_count": errors_count,
        "positive_feedback": positive_feedback,
        "negative_feedback": negative_feedback,
        "top_intents": top_intents,
        "learnings": learnings,
        "tomorrow_focus": tomorrow_focus,
        "generated_at": datetime.now().isoformat(),
    }

    # Persist recap
    recap_path = _RECAPS_DIR / f"{date_label}.json"
    recap_path.write_text(json.dumps(recap, ensure_ascii=False, indent=2), encoding="utf-8")

    return recap


def save_recap_to_obsidian(recap: Dict[str, Any]) -> str:
    """Save recap as Markdown to JarvisVault/daily_recaps/<date>.md via backend."""
    date_label = recap.get("date", date.today().isoformat())
    content = _format_recap_as_markdown(recap)
    path = f"daily_recaps/{date_label}.md"

    try:
        import sys
        root = str(_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        import urllib.request
        import urllib.parse
        import os
        backend = os.getenv("BACKEND_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
        payload = json.dumps({
            "content": content,
            "title": f"Daily Recap — {date_label}",
            "path": path,
        }).encode("utf-8")
        req = urllib.request.Request(
            backend + "/api/jarvis/tools/obsidian/save",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
        return result.get("path") or path
    except Exception as exc:
        logger.warning("[recap_obsidian] save error: %s", exc)
        # Fallback: write locally
        local_dir = _ROOT / "state" / "obsidian_runtime" / "daily_recaps"
        local_dir.mkdir(parents=True, exist_ok=True)
        local_path = local_dir / f"{date_label}.md"
        local_path.write_text(content, encoding="utf-8")
        return str(local_path)


def _format_recap_as_markdown(recap: Dict[str, Any]) -> str:
    d = recap.get("date", "?")
    lines = [
        f"# Daily Recap — {d}",
        "",
        f"**Generated:** {recap.get('generated_at', '?')[:19]}",
        "",
        "## Statistics",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Tasks completed | {recap.get('tasks_completed', 0)} |",
        f"| Photos generated | {recap.get('photos_generated', 0)} |",
        f"| Errors | {recap.get('errors_count', 0)} |",
        f"| 👍 Positive feedback | {recap.get('positive_feedback', 0)} |",
        f"| 👎 Negative feedback | {recap.get('negative_feedback', 0)} |",
        "",
        "## Top Intents",
        "",
    ]
    for intent in recap.get("top_intents", []):
        lines.append(f"- {intent}")
    lines.extend([
        "",
        "## Learnings",
        "",
    ])
    for learning in recap.get("learnings", []):
        lines.append(f"- {learning}")
    lines.extend([
        "",
        "## Tomorrow's Focus",
        "",
        recap.get("tomorrow_focus", ""),
    ])
    return "\n".join(lines)


def format_recap_for_telegram(recap: Dict[str, Any]) -> str:
    """Format recap as Telegram Markdown message."""
    d = recap.get("date", "?")
    pos = recap.get("positive_feedback", 0)
    neg = recap.get("negative_feedback", 0)
    total = pos + neg
    satisfaction = f"{round(pos / total * 100)}%" if total > 0 else "N/A"

    lines = [
        f"📊 *Дневной отчёт — {d}*",
        "",
        f"✅ Задач выполнено: {recap.get('tasks_completed', 0)}",
        f"📸 Фото создано: {recap.get('photos_generated', 0)}",
        f"🔴 Ошибок: {recap.get('errors_count', 0)}",
        f"👍 Удовлетворённость: {satisfaction}",
        "",
        "🔥 Топ запросов: " + ", ".join(recap.get("top_intents", [])[:3]),
        "",
        f"🎯 Фокус завтра: {recap.get('tomorrow_focus', '?')}",
    ]
    learnings = recap.get("learnings", [])
    if learnings:
        lines.extend(["", "💡 Улучшить:"])
        for l in learnings:
            lines.append(f"  • {l}")
    return "\n".join(lines)


def get_recap(date_str: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Load a previously generated recap for the given date."""
    d = date_str or date.today().isoformat()
    recap_path = _RECAPS_DIR / f"{d}.json"
    if not recap_path.exists():
        return None
    try:
        return json.loads(recap_path.read_text(encoding="utf-8"))
    except Exception:
        return None
