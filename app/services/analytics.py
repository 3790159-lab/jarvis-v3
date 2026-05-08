"""Analytics service for Jarvis dashboard — Phase 43."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

_ROOT = Path(__file__).parent.parent.parent


def _decisions_path() -> Path:
    return _ROOT / "state" / "decisions.jsonl"


def _iter_decisions(days: int = 7) -> List[Dict[str, Any]]:
    """Return decision records from last N days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    records = []
    p = _decisions_path()
    if not p.exists():
        return records
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
            if rec.get("timestamp", "") >= cutoff:
                records.append(rec)
        except Exception:
            pass
    return records


def get_decisions_timeseries(days: int = 7) -> List[Dict[str, Any]]:
    """Daily decision counts for last N days. Returns [{date, count}]."""
    records = _iter_decisions(days)
    daily: Counter = Counter()
    for rec in records:
        ts = rec.get("timestamp", "")[:10]  # YYYY-MM-DD
        if ts:
            daily[ts] += 1

    result = []
    today = datetime.now(timezone.utc).date()
    for i in range(days - 1, -1, -1):
        d = str(today - timedelta(days=i))
        result.append({"date": d, "count": daily.get(d, 0)})
    return result


def get_intent_distribution(days: int = 7) -> Dict[str, int]:
    """Counts per intent for last N days."""
    records = _iter_decisions(days)
    counts: Counter = Counter()
    for rec in records:
        intent = rec.get("intent_chosen") or rec.get("intent") or "unknown"
        counts[intent] += 1
    return dict(counts.most_common(10))


def get_success_rate_over_time(days: int = 7) -> List[Dict[str, Any]]:
    """Success rate per day (positive / total feedback)."""
    records = _iter_decisions(days)
    daily_pos: Counter = Counter()
    daily_total: Counter = Counter()
    for rec in records:
        ts = rec.get("timestamp", "")[:10]
        if not ts:
            continue
        feedback = rec.get("feedback")
        if feedback in ("positive", "negative"):
            daily_total[ts] += 1
            if feedback == "positive":
                daily_pos[ts] += 1

    result = []
    today = datetime.now(timezone.utc).date()
    for i in range(days - 1, -1, -1):
        d = str(today - timedelta(days=i))
        total = daily_total.get(d, 0)
        pos = daily_pos.get(d, 0)
        rate = round(pos / total * 100, 1) if total > 0 else None
        result.append({"date": d, "success_rate": rate, "total_feedback": total})
    return result


def estimate_costs(days: int = 7) -> Dict[str, Any]:
    """Approximate API costs based on agent usage counts."""
    records = _iter_decisions(days)
    counts = get_intent_distribution(days)

    # Very rough cost estimates per call
    COST_PER_INTENT = {
        "research": 0.003,
        "brain": 0.008,
        "engineer": 0.012,
        "simple_question": 0.001,
        "generate": 0.04,   # Replicate FLUX per image
        "table": 0.005,
    }

    total = 0.0
    breakdown = {}
    for intent, count in counts.items():
        cost = COST_PER_INTENT.get(intent, 0.002) * count
        total += cost
        breakdown[intent] = round(cost, 4)

    return {
        "total_usd": round(total, 4),
        "breakdown": breakdown,
        "period_days": days,
        "total_calls": len(records),
    }


def get_performance_metrics() -> Dict[str, Any]:
    """Average latency per intent (from execution_time_ms field)."""
    records = _iter_decisions(7)
    latency_sum: defaultdict = defaultdict(float)
    latency_count: defaultdict = defaultdict(int)

    for rec in records:
        intent = rec.get("intent_chosen") or rec.get("intent") or "unknown"
        ms = rec.get("execution_time_ms")
        if ms is not None:
            try:
                latency_sum[intent] += float(ms)
                latency_count[intent] += 1
            except (TypeError, ValueError):
                pass

    result = {}
    for intent in latency_count:
        result[intent] = round(latency_sum[intent] / latency_count[intent], 1)
    return result
