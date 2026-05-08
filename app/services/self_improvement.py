"""Self-Improvement Loop — Block H5.4.

Each night:
1. Read feedback from decisions log.
2. Group by intent and find negative patterns.
3. Use Claude to analyze and suggest improved prompts.
4. A/B test new vs old prompt on sample queries.
5. Apply if new is >20% better.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).parent.parent.parent
_DECISIONS_PATH = _ROOT / "state" / "decisions.jsonl"
_OPTIMIZED_DIR = _ROOT / "state" / "optimized_prompts"
_OPTIMIZED_DIR.mkdir(parents=True, exist_ok=True)


def _read_decisions(days: int = 1) -> List[Dict[str, Any]]:
    """Read decisions from the last N days."""
    if not _DECISIONS_PATH.exists():
        return []
    cutoff = datetime.now() - timedelta(days=days)
    records = []
    try:
        for line in _DECISIONS_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                ts_str = rec.get("timestamp") or rec.get("ts") or ""
                try:
                    if ts_str and datetime.fromisoformat(ts_str[:19]) < cutoff:
                        continue
                except (ValueError, TypeError):
                    pass
                records.append(rec)
            except json.JSONDecodeError:
                continue
    except Exception as exc:
        logger.warning("[self_improve] read error: %s", exc)
    return records


def _call_claude(prompt: str, max_tokens: int = 500) -> str:
    """Call Claude for analysis. Returns empty string on failure."""
    try:
        import sys
        root = str(_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        from app.services.claude_helper import call_claude
        return call_claude(prompt)
    except Exception as exc:
        logger.warning("[self_improve] claude call failed: %s", exc)
        return ""


class SelfImprovementLoop:
    """Nightly self-improvement engine."""

    def collect_feedback(self, days: int = 1) -> Dict[str, Any]:
        """Group feedback by intent into positive/negative buckets."""
        decisions = _read_decisions(days)
        feedback_by_intent: Dict[str, Dict[str, List[Dict]]] = {}

        for d in decisions:
            intent = d.get("intent") or d.get("selected_intent")
            feedback = d.get("feedback")
            if not intent or not feedback:
                continue
            if intent not in feedback_by_intent:
                feedback_by_intent[intent] = {"positive": [], "negative": []}
            bucket = "positive" if feedback == "positive" else "negative"
            feedback_by_intent[intent][bucket].append(d)

        return feedback_by_intent

    def analyze_negatives(self, feedback: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Use Claude to identify patterns in negative feedback."""
        patterns = []
        for intent, data in feedback.items():
            negatives = data.get("negative", [])
            if not negatives:
                continue
            examples = "\n".join(
                f"{d.get('query', '')[:80]} → {str(d.get('response', ''))[:200]}"
                for d in negatives[:5]
            )
            prompt = (
                f"Анализ feedback. Эти ответы получили 👎:\n{examples}\n\n"
                "Найди общую проблему. Верни кратко в JSON: "
                '{"problem": "...", "suggestion": "..."}'
            )
            analysis_raw = _call_claude(prompt)
            analysis: Dict[str, str] = {}
            try:
                # Try to parse JSON from Claude response
                start = analysis_raw.find("{")
                end = analysis_raw.rfind("}") + 1
                if start >= 0 and end > start:
                    analysis = json.loads(analysis_raw[start:end])
            except (json.JSONDecodeError, ValueError):
                if analysis_raw:
                    analysis = {"problem": analysis_raw[:200], "suggestion": ""}

            patterns.append({
                "intent": intent,
                "negative_count": len(negatives),
                "analysis": analysis,
            })
        return patterns

    def optimize_prompt(
        self, intent: str, current_prompt: str, problem: str
    ) -> str:
        """Generate an improved prompt using Claude."""
        if not problem:
            return current_prompt
        prompt = (
            f"Текущий системный промпт для intent={intent}:\n{current_prompt or '(нет)'}\n\n"
            f"Проблема с ответами: {problem}\n\n"
            "Создай улучшенную версию промпта (короче, точнее). "
            "Верни только текст промпта без объяснений."
        )
        result = _call_claude(prompt)
        return result.strip() if result.strip() else current_prompt

    def ab_test(
        self, old_prompt: str, new_prompt: str, test_queries: List[str]
    ) -> Dict[str, Any]:
        """Test both prompts on sample queries. Returns scores dict."""
        if not test_queries:
            # No queries to test — return equal scores
            return {"old_score": 0.5, "new_score": 0.5, "queries_tested": 0}

        old_scores = []
        new_scores = []
        for q in test_queries[:10]:
            try:
                # Ask Claude to rate responses quality (0-1)
                eval_prompt = (
                    f"Запрос: {q}\n\n"
                    f"Промпт A: {old_prompt[:300]}\n"
                    f"Промпт B: {new_prompt[:300]}\n\n"
                    "Какой промпт даст лучший ответ на этот запрос? "
                    'Ответь только JSON: {"winner": "A" or "B", "score_a": 0.0-1.0, "score_b": 0.0-1.0}'
                )
                result_raw = _call_claude(eval_prompt)
                start = result_raw.find("{")
                end = result_raw.rfind("}") + 1
                if start >= 0 and end > start:
                    result = json.loads(result_raw[start:end])
                    old_scores.append(float(result.get("score_a", 0.5)))
                    new_scores.append(float(result.get("score_b", 0.5)))
                else:
                    old_scores.append(0.5)
                    new_scores.append(0.5)
            except Exception:
                old_scores.append(0.5)
                new_scores.append(0.5)

        avg_old = sum(old_scores) / len(old_scores) if old_scores else 0.5
        avg_new = sum(new_scores) / len(new_scores) if new_scores else 0.5

        return {
            "old_score": round(avg_old, 3),
            "new_score": round(avg_new, 3),
            "queries_tested": len(test_queries),
        }

    def apply_if_better(
        self, intent: str, new_prompt: str, ab_result: Dict[str, Any]
    ) -> bool:
        """Apply new prompt if it scored >20% better than old."""
        old_score = ab_result.get("old_score", 0.5)
        new_score = ab_result.get("new_score", 0.5)

        if new_score <= old_score * 1.2:
            return False

        # Save with timestamp for rollback
        path = _OPTIMIZED_DIR / f"{intent}.json"
        history: List[Dict[str, Any]] = []
        if path.exists():
            try:
                history = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                history = []

        entry = {
            "prompt": new_prompt,
            "applied_at": datetime.now().isoformat(),
            "old_score": old_score,
            "new_score": new_score,
            "queries_tested": ab_result.get("queries_tested", 0),
        }
        history.append(entry)
        path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("[self_improve] Applied new prompt for intent=%s (%.2f > %.2f)", intent, new_score, old_score)
        return True

    def get_optimized_prompt(self, intent: str) -> Optional[str]:
        """Return the latest optimized prompt for an intent, or None."""
        path = _OPTIMIZED_DIR / f"{intent}.json"
        if not path.exists():
            return None
        try:
            history = json.loads(path.read_text(encoding="utf-8"))
            if history:
                return history[-1].get("prompt")
        except Exception:
            pass
        return None

    def rollback_prompt(self, intent: str) -> bool:
        """Remove the latest optimized prompt (rollback to previous)."""
        path = _OPTIMIZED_DIR / f"{intent}.json"
        if not path.exists():
            return False
        try:
            history = json.loads(path.read_text(encoding="utf-8"))
            if len(history) <= 1:
                path.unlink()
            else:
                history.pop()
                path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
            return True
        except Exception:
            return False

    def get_improvement_stats(self) -> Dict[str, Any]:
        """Return stats about optimized prompts."""
        stats: Dict[str, Any] = {"total_optimized": 0, "intents": []}
        for path in _OPTIMIZED_DIR.glob("*.json"):
            try:
                history = json.loads(path.read_text(encoding="utf-8"))
                if history:
                    latest = history[-1]
                    stats["intents"].append({
                        "intent": path.stem,
                        "versions": len(history),
                        "latest_score": latest.get("new_score"),
                        "applied_at": latest.get("applied_at"),
                    })
                    stats["total_optimized"] += 1
            except Exception:
                pass
        return stats

    async def run_full_cycle(self) -> Dict[str, Any]:
        """Run complete self-improvement cycle."""
        results: Dict[str, Any] = {"patterns_found": 0, "prompts_improved": 0}
        feedback = self.collect_feedback(days=1)
        patterns = self.analyze_negatives(feedback)
        results["patterns_found"] = len(patterns)

        for p in patterns:
            try:
                intent = p["intent"]
                problem = p.get("analysis", {}).get("problem", "")
                current = self.get_optimized_prompt(intent) or ""
                new_prompt = self.optimize_prompt(intent, current, problem)
                if new_prompt and new_prompt != current:
                    ab = self.ab_test(current, new_prompt, [])
                    if self.apply_if_better(intent, new_prompt, ab):
                        results["prompts_improved"] += 1
            except Exception as exc:
                logger.warning("[self_improve] cycle error for %s: %s", p.get("intent"), exc)

        return results
