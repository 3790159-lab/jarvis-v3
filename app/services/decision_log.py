"""Phase 29: Decision Log — records every routing decision for self-improvement.

Each decision is written to state/decisions.jsonl (append-only).
User feedback (👍/👎) is stored per message_id.
/improve command reads recent decisions and triggers Claude analysis.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DECISIONS_PATH = Path("state") / "decisions.jsonl"
DECISIONS_PATH.parent.mkdir(parents=True, exist_ok=True)

# In-memory map: message_id -> decision_id (for feedback callback)
_FEEDBACK_MAP: Dict[str, str] = {}


# ---------------------------------------------------------------------------
# Core logging
# ---------------------------------------------------------------------------

def log_decision(
    user_query: str,
    intent_chosen: str,
    agent_used: str,
    execution_time_ms: float = 0.0,
    cost_usd: float = 0.0,
    outcome: str = "success",
    message_id: Optional[str] = None,
) -> str:
    """Append a routing decision record to the log. Returns decision_id."""
    decision_id = uuid.uuid4().hex[:16]
    record = {
        "decision_id": decision_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user_query": user_query[:500],
        "intent_chosen": intent_chosen,
        "agent_used": agent_used,
        "user_feedback": None,
        "execution_time_ms": round(execution_time_ms, 1),
        "cost_usd": round(cost_usd, 6),
        "outcome": outcome,
    }
    try:
        with DECISIONS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("Failed to write decision log: %s", exc)

    if message_id:
        _FEEDBACK_MAP[str(message_id)] = decision_id

    return decision_id


def record_feedback(message_id: str, feedback: str, detail: str = "") -> bool:
    """Update feedback for a decision identified by message_id.

    feedback: "positive" | "negative"
    Returns True if the decision was found and updated.
    """
    decision_id = _FEEDBACK_MAP.get(str(message_id))
    if not decision_id:
        return False

    lines = _read_all_lines()
    updated = False
    new_lines = []
    for line in lines:
        try:
            rec = json.loads(line)
            if rec.get("decision_id") == decision_id:
                rec["user_feedback"] = feedback
                if detail:
                    rec["feedback_detail"] = detail[:200]
                new_lines.append(json.dumps(rec, ensure_ascii=False))
                updated = True
            else:
                new_lines.append(line.rstrip())
        except Exception:
            new_lines.append(line.rstrip())

    if updated:
        try:
            DECISIONS_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to update feedback: %s", exc)

    return updated


def get_recent_decisions(n: int = 100) -> List[Dict[str, Any]]:
    """Return the N most recent decision records."""
    lines = _read_all_lines()
    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except Exception:
            pass
    return records[-n:]


def get_stats(n: int = 100) -> Dict[str, Any]:
    """Compute aggregate stats over recent decisions."""
    records = get_recent_decisions(n)
    if not records:
        return {
            "total": 0,
            "success_rate": 0.0,
            "avg_cost_usd": 0.0,
            "avg_latency_ms": 0.0,
            "intents": {},
            "positive_feedback": 0,
            "negative_feedback": 0,
        }

    total = len(records)
    successes = sum(1 for r in records if r.get("outcome") == "success")
    total_cost = sum(r.get("cost_usd", 0.0) for r in records)
    total_latency = sum(r.get("execution_time_ms", 0.0) for r in records)
    positive = sum(1 for r in records if r.get("user_feedback") == "positive")
    negative = sum(1 for r in records if r.get("user_feedback") == "negative")

    intents: Dict[str, int] = {}
    for r in records:
        intent = r.get("intent_chosen", "unknown")
        intents[intent] = intents.get(intent, 0) + 1

    return {
        "total": total,
        "success_rate": round(successes / total * 100, 1) if total else 0.0,
        "avg_cost_usd": round(total_cost / total, 6) if total else 0.0,
        "avg_latency_ms": round(total_latency / total, 1) if total else 0.0,
        "intents": dict(sorted(intents.items(), key=lambda x: -x[1])),
        "positive_feedback": positive,
        "negative_feedback": negative,
    }


# ---------------------------------------------------------------------------
# Analysis via Claude
# ---------------------------------------------------------------------------

def analyze_decisions(n: int = 50) -> str:
    """Send recent decisions to Claude for self-improvement analysis.

    Returns improvement suggestions as a formatted string.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return "ANTHROPIC_API_KEY не задан — анализ недоступен."

    records = get_recent_decisions(n)
    if not records:
        return "Нет данных для анализа. Отправьте несколько запросов сначала."

    # Summarize for analysis (avoid token overrun)
    negative = [r for r in records if r.get("user_feedback") == "negative"]
    intent_counts: Dict[str, int] = {}
    for r in records:
        intent = r.get("intent_chosen", "unknown")
        intent_counts[intent] = intent_counts.get(intent, 0) + 1

    summary = {
        "total_decisions": len(records),
        "negative_feedback_count": len(negative),
        "intent_distribution": intent_counts,
        "negative_examples": [
            {
                "query": r.get("user_query", "")[:100],
                "intent": r.get("intent_chosen"),
                "agent": r.get("agent_used"),
                "detail": r.get("feedback_detail", ""),
            }
            for r in negative[:10]
        ],
    }

    prompt = (
        "You are analyzing routing decisions for Jarvis AI assistant.\n\n"
        f"Summary of last {n} decisions:\n{json.dumps(summary, ensure_ascii=False, indent=2)}\n\n"
        "Based on negative feedback and patterns, suggest 2-3 specific improvements:\n"
        "1. Which intents are being misclassified?\n"
        "2. What keywords/patterns should be added or removed from routing rules?\n"
        "3. What prompt changes would improve answer quality?\n\n"
        "Format your response in Russian. Be specific and actionable."
    )

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as exc:
        logger.warning("analyze_decisions failed: %s", exc)
        return f"Ошибка анализа: {exc}"


def format_stats_message(n: int = 100) -> str:
    """Format statistics for Telegram display."""
    stats = get_stats(n)
    if stats["total"] == 0:
        return "📊 Нет данных. Начните использовать Jarvis!"

    lines = [
        f"📊 Статистика ({stats['total']} решений):",
        f"✅ Success rate: {stats['success_rate']}%",
        f"💰 Средняя стоимость: ${stats['avg_cost_usd']}",
        f"⚡ Средняя latency: {stats['avg_latency_ms']}ms",
        f"👍 Хорошо: {stats['positive_feedback']}  👎 Плохо: {stats['negative_feedback']}",
        "\nПо интентам:",
    ]
    for intent, count in list(stats["intents"].items())[:5]:
        lines.append(f"  {intent}: {count}")

    lines.append("\n/improve analyze — анализ и улучшение")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Inline feedback keyboard builder
# ---------------------------------------------------------------------------

def make_feedback_keyboard(decision_id: str) -> Dict[str, Any]:
    """Build inline keyboard for 👍/👎 feedback."""
    return {
        "inline_keyboard": [[
            {"text": "👍", "callback_data": f"feedback:positive:{decision_id}"},
            {"text": "👎", "callback_data": f"feedback:negative:{decision_id}"},
        ]]
    }


def parse_feedback_callback(data: str) -> Optional[Dict[str, str]]:
    """Parse callback_data from feedback keyboard.

    Returns {"type": "positive"|"negative", "decision_id": "..."} or None.
    """
    parts = data.split(":", 2)
    if len(parts) == 3 and parts[0] == "feedback":
        return {"type": parts[1], "decision_id": parts[2]}
    return None


def record_feedback_by_decision_id(decision_id: str, feedback: str, detail: str = "") -> bool:
    """Update feedback directly by decision_id (not message_id)."""
    lines = _read_all_lines()
    updated = False
    new_lines = []
    for line in lines:
        try:
            rec = json.loads(line)
            if rec.get("decision_id") == decision_id:
                rec["user_feedback"] = feedback
                if detail:
                    rec["feedback_detail"] = detail[:200]
                new_lines.append(json.dumps(rec, ensure_ascii=False))
                updated = True
            else:
                new_lines.append(line.rstrip())
        except Exception:
            new_lines.append(line.rstrip())

    if updated:
        try:
            DECISIONS_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to update feedback: %s", exc)

    return updated


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _read_all_lines() -> List[str]:
    if not DECISIONS_PATH.exists():
        return []
    try:
        return [l for l in DECISIONS_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    except Exception:
        return []
